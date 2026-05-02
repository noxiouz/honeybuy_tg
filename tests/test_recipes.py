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
    assert parsed.recipe_text is None


def test_parse_pasted_recipe_learning_request():
    parsed = parse_learn_recipe_request(
        """
        Запомни рецепт блины
        Ингредиенты:
        - flour 200 g
        - milk 300 ml
        Приготовление:
        Mix and fry.
        """
    )

    assert parsed is not None
    assert parsed.name == "блины"
    assert parsed.url is None
    assert "flour 200 g" in parsed.recipe_text


def test_parse_pasted_recipe_learning_request_with_url_prefers_text():
    parsed = parse_learn_recipe_request(
        """
        Save recipe pancakes
        Source: https://example.com/pancakes
        Ingredients:
        - flour 200 g
        - milk 300 ml
        Method:
        Mix and fry.
        """
    )

    assert parsed is not None
    assert parsed.name == "pancakes"
    assert parsed.url is None
    assert parsed.recipe_text is not None
    assert "Source: https://example.com/pancakes" in parsed.recipe_text
    assert "- flour 200 g" in parsed.recipe_text


def test_parse_pasted_recipe_learning_request_uses_title_line():
    parsed = parse_learn_recipe_request(
        """
        Выучи рецепт
        Pancakes
        Ingredients:
        - flour 200 g
        - milk 300 ml
        Method:
        Mix and fry.
        """
    )

    assert parsed is not None
    assert parsed.name == "Pancakes"
    assert parsed.url is None


def test_parse_english_pasted_recipe_learning_request():
    parsed = parse_learn_recipe_request(
        """
        Save recipe pancakes
        Ingredients:
        - flour 200 g
        - milk 300 ml
        Method:
        Mix and fry.
        """
    )

    assert parsed is not None
    assert parsed.name == "pancakes"
    assert parsed.url is None


def test_parse_add_recipe_request():
    parsed = parse_add_recipe_request("добавь все для солянки")

    assert parsed is not None
    assert parsed.name == "солянки"


def test_ai_recipe_command_detection_is_selective():
    assert should_try_ai_recipe_command("купи на солянку")
    assert should_try_ai_recipe_command("ингредиенты для солянки")
    assert should_try_ai_recipe_command("save recipe pancakes")
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
    assert parsed.recipe_text is None
    learned = recipe_command_from_ai(
        {
            "action": "learn_recipe",
            "recipe_name": "блины",
            "url": None,
            "recipe_text": "Ingredients: flour, milk",
        }
    )
    assert learned.action == "learn_recipe"
    assert learned.recipe_text == "Ingredients: flour, milk"
    learned_without_body = recipe_command_from_ai(
        {"action": "learn_recipe", "recipe_name": "блины", "url": None}
    )
    assert learned_without_body.action == "learn_recipe"
    assert learned_without_body.recipe_text is None
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
