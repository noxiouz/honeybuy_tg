from honeybuy_tg.parser import ParsedAction, parse_shopping_text


def test_parse_add_items_from_russian_voice_transcript():
    parsed = parse_shopping_text("Купи молоко и хлеб")

    assert parsed.action == ParsedAction.ADD_ITEMS
    assert parsed.items == ("молоко", "хлеб")


def test_parse_remove_item_from_russian_voice_transcript():
    parsed = parse_shopping_text("удали молоко")

    assert parsed.action == ParsedAction.REMOVE_ITEMS
    assert parsed.items == ("молоко",)


def test_parse_bought_item_from_russian_voice_transcript():
    parsed = parse_shopping_text("молоко куплено")

    assert parsed.action == ParsedAction.MARK_BOUGHT
    assert parsed.items == ("молоко",)


def test_parse_bought_item_from_suffix_phrase():
    parsed = parse_shopping_text("яйца купил")

    assert parsed.action == ParsedAction.MARK_BOUGHT
    assert parsed.items == ("яйца",)


def test_parse_bought_item_from_prefix_phrase():
    parsed = parse_shopping_text("купил хлеб")

    assert parsed.action == ParsedAction.MARK_BOUGHT
    assert parsed.items == ("хлеб",)


def test_parse_show_list():
    parsed = parse_shopping_text("покажи список")

    assert parsed.action == ParsedAction.SHOW_LIST
    assert parsed.items == ()


def test_unknown_command_needs_confirmation():
    parsed = parse_shopping_text("что-то непонятное")

    assert parsed.action == ParsedAction.UNKNOWN
    assert parsed.needs_confirmation


def test_bare_voice_item_list_can_default_to_add():
    parsed = parse_shopping_text(
        "Яйца и масло.",
        default_action=ParsedAction.ADD_ITEMS,
    )

    assert parsed.action == ParsedAction.ADD_ITEMS
    assert parsed.items == ("яйца", "масло")


def test_local_parser_filters_filler_items():
    parsed = parse_shopping_text(
        "пожалуйста, купи воды, водки, немного помидоров, десяток яиц, хлеба, "
        "что еще?, мусорные пакеты"
    )

    assert parsed.action == ParsedAction.ADD_ITEMS
    assert parsed.items == (
        "воды",
        "водки",
        "немного помидоров",
        "десяток яиц",
        "хлеба",
        "мусорные пакеты",
    )
