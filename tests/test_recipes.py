from honeybuy_tg.recipes import (
    html_to_text,
    parse_add_recipe_request,
    parse_learn_recipe_request,
    looks_like_recipe_reuse_request,
    recipe_command_from_ai,
    should_try_ai_recipe_command,
)


def test_parse_learn_recipe_request():
    parsed = parse_learn_recipe_request(
        "Выучи солянку - вот ссылка https://cookidoo.co.uk/recipes/recipe/en-GB/r769287"
    )

    assert parsed is not None
    assert parsed.name == "солянку"
    assert parsed.url == "https://cookidoo.co.uk/recipes/recipe/en-GB/r769287"


def test_parse_add_recipe_request():
    parsed = parse_add_recipe_request("добавь все для солянки")

    assert parsed is not None
    assert parsed.name == "солянки"


def test_ai_recipe_command_detection_is_selective():
    assert should_try_ai_recipe_command("купи на солянку")
    assert should_try_ai_recipe_command("ингредиенты для солянки")
    assert not should_try_ai_recipe_command("купи молоко")
    assert looks_like_recipe_reuse_request("купи на солянку")
    assert not looks_like_recipe_reuse_request("купи молоко")


def test_recipe_command_from_ai():
    parsed = recipe_command_from_ai(
        {"action": "add_recipe", "recipe_name": "солянка", "url": None}
    )

    assert parsed.action == "add_recipe"
    assert parsed.name == "солянка"
    assert parsed.url is None
    assert recipe_command_from_ai({"action": "add_recipe"}).action == "unknown"


def test_html_to_text_omits_script_and_keeps_visible_text():
    text = html_to_text(
        """
        <html>
          <body>
            <h1>Solyanka</h1>
            <script>hidden()</script>
            <p>fresh dill</p>
          </body>
        </html>
        """
    )

    assert "Solyanka" in text
    assert "fresh dill" in text
    assert "hidden" not in text
