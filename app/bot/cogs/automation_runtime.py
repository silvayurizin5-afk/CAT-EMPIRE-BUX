import json
import re
import time
from types import MethodType

import discord

from app.bot.cogs import automation as base
from app.bot.components_v2 import CardLayout, strip_generic_emoji
from app.services.ai_gateway import AIUnavailable, request_structured_ai

GENERAL_AI_PROMPT = (
    base.AI_SYSTEM_PROMPT
    + """

Regras adicionais de interpretação e atendimento:
- Este canal NÃO é apenas uma calculadora. Ele também atende dúvidas gerais, perguntas sobre a loja,
  catálogo, jogos, produtos, preços e conversa geral.
- Nunca mande uma pergunta comum para o suporte só por não ser cálculo. Perguntas gerais devem usar
  intent other e receber uma resposta útil em reply.
- Use unclear SOMENTE quando o usuário estiver tentando comprar/calcular algo e faltar um dado
  indispensável. Nunca use unclear para uma pergunta normal de conhecimento ou conversa.
- Perguntas como "estoque", "qual o estoque atual?", "o que tem disponível?", "quais produtos vocês
  têm?" são dúvidas gerais da loja. Use store_question; NÃO use catalog sem um produto/jogo específico.
- Perguntas como "quais jogos tem?", "quais os jogos da loja?" ou "quais jogos estão disponíveis?"
  são store_question.
- Perguntas sobre cupom, como "como funcionam os cupons?", são store_question.
- Se o usuário perguntar o preço de um produto cadastrado pelo nome, trate como consulta de catálogo
  e use o preço cadastrado. Não transforme automaticamente isso em cálculo por Robux.
- "Quanto custa uma Game Pass de N Robux?" é uma pergunta de cálculo e NÃO exige nome de jogo.
- Considere o contexto recente fornecido em conversation_context para frases curtas como
  "quanto custa?", "e quais Game Pass?" ou "e desse jogo?".
- Game Pass é expressão de gênero feminino em português. Use sempre "a Game Pass", "uma Game Pass",
  "da Game Pass"; nunca "o gamepass".
- Não invente dados da NEXTBUY. Para fatos específicos da loja, use apenas facts e catalog.
- Para perguntas gerais fora da loja, responda normalmente com conhecimento geral no campo reply.
- Para perguntas gerais, continue sem emoji Unicode e sugira no máximo um emoji customizado existente.
- Não forneça instruções perigosas, ilegais ou que facilitem dano.

Exemplos:
Usuário: "Estoque" -> store_question.
Usuário: "Quais jogos tem?" -> store_question.
Usuário: "E quais Game Pass?" -> store_question, usando conversation_context se houver.
Usuário: "Quanto custa esse Notifier?" -> consulta do produto Notifier; não pedir quantidade de Robux.
Usuário: "Quanto custa uma Game Pass de 500 Robux?" -> cálculo de Game Pass; jogo não é obrigatório.
Usuário: "Quem descobriu o Brasil?" -> other com reply útil.
"""
)

GENERAL_QA_PROMPT = """Você é a assistente da NEXTBUY em um canal do Discord que aceita dúvidas gerais
e dúvidas sobre a loja. Responda em português do Brasil, de forma clara, natural e curta.
Saída obrigatória: JSON válido no formato {"reply":"texto"}.

Regras:
- Para fatos da NEXTBUY, use SOMENTE catalog, facts e conversation_context enviados. Não invente.
- Para perguntas gerais de conhecimento, responda normalmente.
- Game Pass é feminino: escreva "a Game Pass", "uma Game Pass", "da Game Pass".
- Não exponha nomes internos de campos, JSON, product_name, game_name ou detalhes técnicos.
- Não use emoji Unicode. Só texto.
- Se a pergunta depender de um dado privado/específico da loja que não está no contexto, retorne
  {"reply":null}.
- Não forneça instruções perigosas, ilegais ou que facilitem dano.
"""

_ORIGINAL_HANDLE_AI = base.AutomationCog._handle_ai
_CONTEXT_TTL_SECONDS = 300.0
_GAMEPASS_TYPES = {"gamepass", "game_pass", "gift"}

_STOCK_QUESTIONS = {
    "estoque",
    "estoque atual",
    "qual o estoque",
    "qual o estoque atual",
    "o que tem em estoque",
    "o que tem disponivel",
    "quais produtos tem",
    "quais produtos estao disponiveis",
    "produtos disponiveis",
}

_GAME_QUESTION_MARKERS = (
    "quais jogos",
    "que jogos",
    "jogos da loja",
    "jogos tem",
    "jogos atuais",
    "jogos disponiveis",
)

_GAMEPASS_LIST_MARKERS = (
    "quais gamepass",
    "quais game pass",
    "quais as gamepass",
    "quais as game pass",
    "que gamepass",
    "que game pass",
    "gamepasses tem",
    "game pass tem",
)

_PRICE_MARKERS = ("quanto custa", "qual o preco", "qual preco", "preco de", "valor de")
_NO_FEE_MARKERS = (
    "sem cobrir a taxa",
    "sem cobrir taxa",
    "sem taxa",
    "valor normal",
    "preco normal",
)
_COVER_FEE_MARKERS = (
    "cobrindo a taxa",
    "cobrir a taxa",
    "com taxa",
)
_VIA_PLUS_MARKERS = ("via plus", "plus")


def _numeric_amount(text: str) -> int | None:
    if not re.fullmatch(r"\s*[\d\s.,]+\s*", text):
        return None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    value = int(digits)
    return value if 0 < value <= 1_000_000 else None


def _robux_amount(text: str) -> int | None:
    match = re.search(r"(\d[\d\s.,]*)\s*robux\b", text, re.IGNORECASE)
    if match is None:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    if not digits:
        return None
    value = int(digits)
    return value if 0 < value <= 1_000_000 else None


def _available_products(products):
    return [product for product in products if base._available(product)]


def _product_type(product) -> str:
    return base._ptype(product)


def _is_gamepass(product) -> bool:
    return _product_type(product) in _GAMEPASS_TYPES


def _match_game(text: str, products) -> str | None:
    normalized = base.normalize_text(text)
    games: dict[str, str] = {}
    for product in _available_products(products):
        if not product.game_name:
            continue
        game_norm = base.normalize_text(product.game_name)
        if game_norm:
            games.setdefault(game_norm, product.game_name)

    exact = games.get(normalized)
    if exact:
        return exact

    candidates = [
        display
        for game_norm, display in games.items()
        if len(normalized) >= 4 and (game_norm in normalized or normalized in game_norm)
    ]
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


def _match_product(text: str, products):
    normalized = base.normalize_text(text)
    matches = []
    for product in _available_products(products):
        name_norm = base.normalize_text(product.name)
        if not name_norm:
            continue
        if normalized == name_norm or (len(name_norm) >= 3 and name_norm in normalized):
            matches.append(product)
    if len(matches) == 1:
        return matches[0]
    exact = [p for p in matches if base.normalize_text(p.name) == normalized]
    return exact[0] if len(exact) == 1 else None


def _find_product_by_name(products, name: str | None):
    if not name:
        return None
    wanted = base.normalize_text(name)
    matches = [
        product
        for product in _available_products(products)
        if base.normalize_text(product.name) == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def _stock_text(product) -> str:
    return (
        "estoque ilimitado"
        if product.stock_quantity is None
        else f"{product.stock_quantity} em estoque"
    )


def _stock_answer(products) -> str:
    available = _available_products(products)
    if not available:
        return "No momento não há produtos ativos com estoque disponível."

    lines = ["Estoque atual da loja:"]
    for product in available[:15]:
        price = (
            base.format_brl(product.price_credits)
            if product.price_credits is not None
            else "preço não cadastrado"
        )
        game = f" • {product.game_name}" if product.game_name else ""
        lines.append(f"- {product.name}{game}: {price} • {_stock_text(product)}")
    if len(available) > 15:
        lines.append(f"- +{len(available) - 15} produto(s) disponível(is)")
    return "\n".join(lines)


def _games_answer(products) -> tuple[str, str | None]:
    games = sorted(
        {
            product.game_name
            for product in _available_products(products)
            if product.game_name
        },
        key=lambda value: base.normalize_text(value),
    )
    if not games:
        return "No momento não há jogos cadastrados com produtos disponíveis.", None
    lines = ["Jogos disponíveis na loja:"]
    lines.extend(f"- **{game}**" for game in games)
    return "\n".join(lines), games[0] if len(games) == 1 else None


def _gamepasses_for(products, game_name: str | None = None):
    result = [p for p in _available_products(products) if _is_gamepass(p)]
    if game_name:
        wanted = base.normalize_text(game_name)
        result = [
            p for p in result if base.normalize_text(p.game_name or "") == wanted
        ]
    return result


def _gamepasses_answer(products, game_name: str | None = None) -> tuple[str, str | None]:
    passes = _gamepasses_for(products, game_name)
    if not passes:
        if game_name:
            return f"Não há Game Pass disponível para **{game_name}** no catálogo atual.", None
        return "Não há Game Pass disponível no catálogo atual.", None

    heading = (
        f"As Game Pass disponíveis para **{game_name}** são:"
        if game_name
        else "As Game Pass disponíveis na loja são:"
    )
    lines = [heading]
    for product in passes[:15]:
        price = (
            base.format_brl(product.price_credits)
            if product.price_credits is not None
            else "preço não cadastrado"
        )
        game = f" • {product.game_name}" if product.game_name and not game_name else ""
        lines.append(f"- **{product.name}**{game} — {price} — {_stock_text(product)}")
    selected = passes[0].name if len(passes) == 1 else None
    return "\n".join(lines), selected


def _game_answer(products, game_name: str) -> tuple[str, str | None]:
    wanted = base.normalize_text(game_name)
    related = [
        p
        for p in _available_products(products)
        if base.normalize_text(p.game_name or "") == wanted
    ]
    if not related:
        return f"**{game_name}** não possui produtos disponíveis no catálogo atual.", None

    lines = [f"**{game_name}** está disponível na loja.", "Produtos disponíveis:"]
    for product in related[:15]:
        price = (
            base.format_brl(product.price_credits)
            if product.price_credits is not None
            else "preço não cadastrado"
        )
        category = "Game Pass" if _is_gamepass(product) else "Item"
        lines.append(f"- **{category}: {product.name}** — {price} — {_stock_text(product)}")
    selected = related[0].name if len(related) == 1 else None
    return "\n".join(lines), selected


def _product_price_answer(product) -> str:
    if product.price_credits is None:
        return f"**{product.name}** está cadastrado, mas ainda não possui preço definido."
    price = base.format_brl(product.price_credits)
    if _is_gamepass(product):
        game = f" de **{product.game_name}**" if product.game_name else ""
        return f"A Game Pass **{product.name}**{game} custa **{price}**."
    if _product_type(product) == "item":
        game = f" de **{product.game_name}**" if product.game_name else ""
        return f"O item **{product.name}**{game} custa **{price}**."
    return f"**{product.name}** custa **{price}**."


def _gamepass_quote_answer(amount: int) -> str:
    quote = base.robux_quote(amount)
    return (
        f"Uma Game Pass de **{base.format_robux(amount)}** custa "
        f"**{base.format_brl(quote.gamepass_brl)}**."
    )


def _robux_followup_answer(normalized: str, amount: int) -> str | None:
    quote = base.robux_quote(amount)
    if any(marker in normalized for marker in _NO_FEE_MARKERS):
        return (
            f"Para **{base.format_robux(amount)}**, sem cobrir a taxa, o valor normal é "
            f"**{base.format_brl(quote.gamepass_brl)}**. "
            f"O **Via Plus usa o mesmo valor: {base.format_brl(quote.via_plus_brl)}**."
        )
    if any(marker in normalized for marker in _COVER_FEE_MARKERS):
        return (
            f"Para **{base.format_robux(amount)}**, cobrindo a taxa, fica "
            f"**{base.format_brl(quote.covering_fee_brl)}**."
        )
    if any(marker in normalized for marker in _VIA_PLUS_MARKERS):
        return (
            f"Para **{base.format_robux(amount)}**, o **Via Plus** custa "
            f"**{base.format_brl(quote.via_plus_brl)}**."
        )
    return None


def _get_state(self, key: tuple[int, int]) -> dict[str, object]:
    now = time.monotonic()
    state = self._conversation_context.get(key)
    if state is None or now - float(state.get("created_at", 0.0)) > _CONTEXT_TTL_SECONDS:
        state = {"created_at": now}
        self._conversation_context[key] = state
    return state


def _remember(
    self,
    key: tuple[int, int],
    *,
    game_name: str | None = None,
    product_name: str | None = None,
    intent: str | None = None,
) -> None:
    state = _get_state(self, key)
    state["created_at"] = time.monotonic()
    if game_name:
        state["game_name"] = game_name
    if product_name:
        state["product_name"] = product_name
    if intent:
        state["intent"] = intent


def _quick_store_answer(
    message: discord.Message,
    products,
    state: dict[str, object],
) -> dict[str, object] | None:
    normalized = base.normalize_text(message.content)
    named_game = _match_game(message.content, products)
    named_product = _match_product(message.content, products)

    remembered_robux = state.get("robux_amount")
    if isinstance(remembered_robux, int) and remembered_robux > 0:
        followup = _robux_followup_answer(normalized, remembered_robux)
        if followup is not None:
            return {
                "intent": "store_question",
                "multiple_requests": False,
                "reply": followup,
                "emoji_name": None,
            }

    if normalized in _STOCK_QUESTIONS:
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": _stock_answer(products),
            "emoji_name": None,
        }

    if any(marker in normalized for marker in _GAME_QUESTION_MARKERS):
        answer, only_game = _games_answer(products)
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": answer,
            "emoji_name": None,
            "_context_game_name": only_game,
        }

    if any(marker in normalized for marker in _GAMEPASS_LIST_MARKERS):
        context_game = named_game or str(state.get("game_name") or "").strip() or None
        answer, only_product = _gamepasses_answer(products, context_game)
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": answer,
            "emoji_name": None,
            "_context_game_name": context_game,
            "_context_product_name": only_product,
        }

    if ("cupom" in normalized or "cupons" in normalized) and (
        normalized in {"cupom", "cupons"}
        or "como" in normalized
        or "funciona" in normalized
        or "usar" in normalized
        or "aplicar" in normalized
    ):
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": (
                "Os cupons são aplicados antes de finalizar a compra pelo botão **Adicionar cupom**. "
                "O bot valida se o código existe, está ativo e ainda possui usos. Quando é válido, "
                "o desconto aparece no valor final antes da criação do pedido."
            ),
            "emoji_name": None,
        }

    if "pagamento" in normalized or normalized in {"pix", "aceita pix", "como paga"}:
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": (
                "O pagamento da NEXTBUY é feito por **PIX**. Depois da criação do pedido, o bot abre "
                "o canal privado com o QR Code e o PIX Copia e Cola; a equipe confirma o pagamento."
            ),
            "emoji_name": None,
        }

    amount = _robux_amount(message.content)
    if (
        amount is not None
        and ("gamepass" in normalized or "game pass" in normalized)
        and any(marker in normalized for marker in _PRICE_MARKERS)
    ):
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": _gamepass_quote_answer(amount),
            "emoji_name": None,
        }

    if any(marker in normalized for marker in _PRICE_MARKERS):
        product = named_product
        if product is None:
            product = _find_product_by_name(products, str(state.get("product_name") or "") or None)
        if product is not None:
            return {
                "intent": "store_question",
                "multiple_requests": False,
                "reply": _product_price_answer(product),
                "emoji_name": None,
                "_context_game_name": product.game_name,
                "_context_product_name": product.name,
            }
        if named_game:
            answer, only_product = _gamepasses_answer(products, named_game)
            return {
                "intent": "store_question",
                "multiple_requests": False,
                "reply": answer,
                "emoji_name": None,
                "_context_game_name": named_game,
                "_context_product_name": only_product,
            }

    if named_game and (
        normalized == base.normalize_text(named_game)
        or "tem " in normalized
        or "disponivel" in normalized
        or "disponiveis" in normalized
    ):
        answer, only_product = _game_answer(products, named_game)
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": answer,
            "emoji_name": None,
            "_context_game_name": named_game,
            "_context_product_name": only_product,
        }

    return None


async def _safe_reply(
    message: discord.Message,
    *,
    title: str,
    lines: list[str],
    footer: str | None = None,
) -> None:
    def build_view() -> CardLayout:
        return CardLayout(
            title=title,
            lines=[message.author.mention, *lines],
            footer=footer,
            timeout=180,
        )

    allowed_mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
    try:
        await message.reply(
            view=build_view(),
            mention_author=False,
            allowed_mentions=allowed_mentions,
        )
    except discord.HTTPException as exc:
        if exc.code != 50035 or "message_reference" not in str(exc):
            raise
        await message.channel.send(
            view=build_view(),
            allowed_mentions=allowed_mentions,
        )


async def _general_answer(
    self,
    message: discord.Message,
    products,
    *,
    state: dict[str, object],
) -> str | None:
    if message.guild is None:
        return None
    order = self._provider_orders.get(message.guild.id)
    payload = {
        "message": message.content,
        "conversation_context": {
            "game_name": state.get("game_name"),
            "product_name": state.get("product_name"),
            "robux_amount": state.get("robux_amount"),
            "last_intent": state.get("intent"),
        },
        "catalog": base._snapshot(products),
        "facts": {
            "currency": "BRL",
            "payment": "PIX com confirmação pela equipe",
            "robux_price_per_100": str(base.ROBUX_PRICE_PER_100),
            "coupons": (
                "O cliente aplica um código pelo botão Adicionar cupom antes de comprar; "
                "o backend valida existência, status e limite de usos."
            ),
        },
    }
    try:
        parsed, _ = await request_structured_ai(
            system_prompt=GENERAL_QA_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            provider_order=order,
        )
    except AIUnavailable:
        return None
    reply = strip_generic_emoji(str(parsed.get("reply") or "").strip())
    return reply or None


async def _interpret(
    self,
    message: discord.Message,
    products,
    provider_order: list[str],
) -> dict[str, object] | None:
    if message.guild is None:
        return None

    key = (message.guild.id, message.author.id)
    state = _get_state(self, key)
    self._provider_orders[message.guild.id] = list(provider_order)

    detected_robux = _robux_amount(message.content)
    if detected_robux is None:
        detected_robux = _numeric_amount(message.content)
    if detected_robux is not None:
        state["robux_amount"] = detected_robux
        state["created_at"] = time.monotonic()

    quick = _quick_store_answer(message, products, state)
    if quick is not None:
        return quick

    payload = {
        "message": message.content,
        "conversation_context": {
            "game_name": state.get("game_name"),
            "product_name": state.get("product_name"),
            "last_intent": state.get("intent"),
        },
        "catalog": base._snapshot(products),
        "server_emoji_names": [emoji.name for emoji in message.guild.emojis[:80]],
        "facts": {
            "currency": "BRL",
            "payment": "PIX com confirmação pela equipe",
            "robux_price_per_100": str(base.ROBUX_PRICE_PER_100),
            "coupons": (
                "O cliente aplica um código pelo botão Adicionar cupom antes de comprar. "
                "O backend valida se o cupom existe, está ativo e ainda possui usos; se válido, "
                "o desconto é aplicado ao total antes da criação do pedido."
            ),
            "inventory": (
                "O catálogo enviado contém os produtos ativos conhecidos pelo backend. "
                "Pergunta geral de estoque deve ser store_question; catalog exige item específico."
            ),
        },
    }
    try:
        parsed, _ = await request_structured_ai(
            system_prompt=GENERAL_AI_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            provider_order=provider_order,
        )
        return parsed
    except AIUnavailable:
        return None


async def _support(
    self,
    message: discord.Message,
    text: str | None,
    support_id: int | None,
    emoji_name: str | None = None,
) -> None:
    if message.guild is None:
        return

    cleaned = strip_generic_emoji(text or "")
    emoji = base._custom_emoji(message.guild, emoji_name)
    if cleaned:
        await base._reply(
            message,
            title="NEXTBUY",
            lines=[f"{emoji + ' ' if emoji else ''}{cleaned}"],
        )
        return

    channel = base._mention_channel(message.guild, support_id)
    await base._reply(
        message,
        title="NEXTBUY",
        lines=[
            f"Não consegui confirmar essa informação específica da loja. Use {channel} para falar com a equipe."
            if channel
            else "Não consegui confirmar essa informação específica da loja. Fale com a equipe."
        ],
    )


def _natural_missing(missing) -> str:
    if not isinstance(missing, list):
        return "Preciso de mais um detalhe para continuar."
    labels = {
        "product_name": "qual produto",
        "game_name": "qual jogo",
        "quantity": "qual quantidade",
        "robux": "quantos Robux",
        "brl": "qual valor em reais",
    }
    values = [labels.get(str(item), str(item).replace("_", " ")) for item in missing]
    values = [value for value in values if value]
    if not values:
        return "Preciso de mais um detalhe para continuar."
    return "Falta informar " + ", ".join(values) + "."


def _looks_like_commerce_request(text: str) -> bool:
    normalized = base.normalize_text(text)
    return any(
        marker in normalized
        for marker in (
            "quero ",
            "comprar",
            "gamepass",
            "game pass",
            "robux",
            "item",
            "produto",
            "cupom",
            "calcular",
        )
    )


async def _handle_ai(
    self,
    message: discord.Message,
    data: dict[str, object],
    products,
    panel,
    support_id: int | None,
    suggestions_id: int | None,
) -> None:
    if message.guild is None:
        return

    key = (message.guild.id, message.author.id)
    state = _get_state(self, key)
    now = time.monotonic()
    pending = self._pending_context.get(key)
    if pending is not None and now - float(pending.get("created_at", 0.0)) > _CONTEXT_TTL_SECONDS:
        self._pending_context.pop(key, None)
        pending = None

    patched = dict(data)
    context_game = str(patched.pop("_context_game_name", "") or "").strip() or None
    context_product = str(patched.pop("_context_product_name", "") or "").strip() or None
    if context_game or context_product:
        _remember(
            self,
            key,
            game_name=context_game,
            product_name=context_product,
            intent=str(patched.get("intent") or "") or None,
        )

    if patched.get("multiple_requests") is True:
        await _ORIGINAL_HANDLE_AI(
            self, message, patched, products, panel, support_id, suggestions_id
        )
        return

    intent = str(patched.get("intent") or "unclear").strip().lower()
    game_name = str(patched.get("game_name") or "").strip() or None
    product_name = str(patched.get("product_name") or "").strip() or None

    if not game_name:
        remembered_game = str(state.get("game_name") or "").strip()
        if remembered_game and intent in {"gamepass", "item", "catalog"}:
            game_name = remembered_game
            patched["game_name"] = remembered_game

    if not product_name:
        remembered_product = str(state.get("product_name") or "").strip()
        if remembered_product and intent in {"item", "catalog"}:
            product_name = remembered_product
            patched["product_name"] = remembered_product

    if game_name or product_name:
        _remember(
            self,
            key,
            game_name=game_name,
            product_name=product_name,
            intent=intent,
        )

    if pending and pending.get("intent") == "gamepass":
        amount = _numeric_amount(message.content)
        pending_game = str(pending.get("game_name") or "").strip()
        if pending_game and amount is not None:
            patched.update(
                {
                    "intent": "gamepass",
                    "game_name": pending_game,
                    "robux": amount,
                    "multiple_requests": False,
                }
            )
            intent = "gamepass"
            game_name = pending_game
            self._pending_context.pop(key, None)

    if intent == "catalog":
        matched_game = _match_game(message.content, products)
        if matched_game:
            answer, only_product = _game_answer(products, matched_game)
            _remember(
                self,
                key,
                game_name=matched_game,
                product_name=only_product,
                intent="store_question",
            )
            await self._support(message, answer, support_id)
            return

    if intent == "gamepass":
        product = _match_product(message.content, products)
        if product is None and product_name:
            product = _find_product_by_name(products, product_name)
        try:
            amount = int(patched.get("robux") or 0)
        except (TypeError, ValueError):
            amount = 0

        if product is not None and _is_gamepass(product) and amount <= 0:
            _remember(
                self,
                key,
                game_name=product.game_name,
                product_name=product.name,
                intent="store_question",
            )
            await self._support(message, _product_price_answer(product), support_id)
            return

        normalized = base.normalize_text(message.content)
        if (
            amount > 0
            and not game_name
            and any(marker in normalized for marker in _PRICE_MARKERS)
        ):
            await self._support(message, _gamepass_quote_answer(amount), support_id)
            return

        if amount <= 0 or not game_name:
            self._pending_context[key] = {
                "intent": "gamepass",
                "game_name": game_name,
                "created_at": now,
            }
        else:
            self._pending_context.pop(key, None)

    if intent == "store_question":
        reply = str(patched.get("reply") or "").strip() or None
        if not reply:
            reply = await _general_answer(self, message, products, state=state)
        await self._support(
            message,
            reply,
            support_id,
            str(patched.get("emoji_name") or "").strip() or None,
        )
        return

    if intent == "other":
        reply = str(patched.get("reply") or "").strip() or None
        if not reply:
            reply = await _general_answer(self, message, products, state=state)
        await self._support(
            message,
            reply,
            support_id,
            str(patched.get("emoji_name") or "").strip() or None,
        )
        return

    if intent == "unclear":
        if not _looks_like_commerce_request(message.content):
            reply = await _general_answer(self, message, products, state=state)
            if reply:
                await self._support(message, reply, support_id)
                return
        await base._reply(
            message,
            title="Preciso de mais detalhes",
            lines=[_natural_missing(patched.get("missing"))],
        )
        return

    await _ORIGINAL_HANDLE_AI(
        self,
        message,
        patched,
        products,
        panel,
        support_id,
        suggestions_id,
    )


async def setup(bot) -> None:
    base._reply = _safe_reply
    cog = base.AutomationCog(bot)
    cog._pending_context = {}
    cog._conversation_context = {}
    cog._provider_orders = {}
    cog._interpret = MethodType(_interpret, cog)
    cog._support = MethodType(_support, cog)
    cog._handle_ai = MethodType(_handle_ai, cog)
    await bot.add_cog(cog)
