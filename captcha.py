import re

def solve_math_captcha(captcha_text):
    """
    Parses and solves a simple arithmetic captcha validation string.
    Expected formats:
    - "Berapa 10 ditambah 5 ?"
    - "Berapa 10 dikurangi 5 ?"
    - "Berapa 10 dikali 5 ?"
    - "Berapa 10 dibagi 5 ?"
    """
    text = captcha_text.strip()
    
    # Ex:
    # "Berapa 10 ditambah 5 ?"
    # "Berapa hasil dari 10 ditambah 5 ?"
    # "Hasil dari 10 ditambah 5 ?"
    # "Hitunglah 10 ditambah 5 ?"
    match = re.search(r'(?:Berapa\s+)?(?:hasil\s+dari\s+)?(?:Hitunglah\s+)?(\d+)\s+(ditambah|dikurangi|dikali|dibagi)\s+(\d+)\s*\?', text, re.IGNORECASE)
    
    if not match:
        raise ValueError(f"Could not parse captcha text: '{captcha_text}'")
    
    num1 = int(match.group(1))
    operation = match.group(2).lower()
    num2 = int(match.group(3))
    
    if operation == 'ditambah':
        return num1 + num2
    elif operation == 'dikurangi':
        return num1 - num2
    elif operation == 'dikali':
        return num1 * num2
    elif operation == 'dibagi':
        return int(num1 / num2)
    else:
        raise ValueError(f"Unknown operation: {operation}")

if __name__ == "__main__":
    test_cases = [
        ("Berapa 10 ditambah 5 ?", 15),
        ("Berapa 10 dikurangi 2 ?", 8),
        ("Berapa 3 dikali 4 ?", 12),
        ("Berapa 20 dibagi 4 ?", 5),
        ("Hasil dari 1 dikurangi 1 ?", 0),
        ("Berapa hasil dari 7 ditambah 1 ?", 8),
    ]
    
    for text, expected in test_cases:
        result = solve_math_captcha(text)
        print(f"'{text}' -> {result} (Expected: {expected})")
        assert result == expected
    print("All tests passed!")
