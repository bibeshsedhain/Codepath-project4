"""Illustrative inputs, not an independently labeled evaluation corpus."""
SAMPLES = {
    'uniform_ai_like': ' '.join(['The system provides consistent value for every user.'] * 8),
    'casual_human_like': (
        'Rain. I ran. Why did I forget my coat again? '
        'The bus driver waved me across the puddle, grinning like he knew exactly how my morning had gone. '
        'Anyway, after forty minutes of standing in soggy socks beside a woman carrying an enormous cardboard '
        'bird, I got to campus and realized that the notebook I needed was still on the kitchen table underneath '
        'yesterday’s grocery receipt and three unopened letters from my landlord.'),
    'formal_borderline': (
        'The relationship between monetary policy and asset price inflation has been extensively studied in the literature. '
        'Central banks face a fundamental tension between their mandate for price stability and the unintended '
        'consequences of prolonged low interest rates on equity and real estate valuations.'),
    'edited_borderline': (
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs — flexibility and no commute "
        'on one side, isolation and blurred work-life boundaries on the other. Studies show productivity varies widely '
        'by individual and role type.'),
}
