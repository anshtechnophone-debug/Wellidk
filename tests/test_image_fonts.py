from app.services.image_fonts import font_for_text, script_for_text


def test_script_detection_covers_common_user_name_scripts():
    assert script_for_text("Ansh") == "default"
    assert script_for_text("अनुज") == "devanagari"
    assert script_for_text("ਪੰਜਾਬ") == "gurmukhi"
    assert script_for_text("বাংলা") == "bengali"
    assert script_for_text("مرحبا") == "arabic"
    assert script_for_text("東京") == "cjk"


def test_decorative_latin_is_normalized_for_font_selection():
    assert script_for_text("𝓐𝓷𝓼𝓱") == "default"


def test_font_loader_has_safe_fallback():
    assert font_for_text("नमस्ते", 24, True) is not None
    assert font_for_text("XYRON", 24, False) is not None
