"""Cabinet geometry drawn from the owner's reference photo.

Positions describe the panel, not encoder IDs. Keep physical buttons unlabelled
until a live calibration establishes their wiring.
"""

import pygame


BUTTON_POSITIONS = {
    "top_left": (142, 23), "top_middle_left": (168, 23),
    "top_middle_right": (190, 23), "top_right": (216, 23),
    "left_lower": (88, 62), "left_middle": (105, 52), "left_upper": (122, 42),
    "right_lower": (270, 62), "right_middle": (287, 52), "right_upper": (304, 42),
}

BUTTON_PROMPTS = {
    "top_left": "Top row: left WHITE button",
    "top_middle_left": "Top row: left BLACK button",
    "top_middle_right": "Top row: right BLACK button",
    "top_right": "Top row: right WHITE button",
    "left_lower": "Left group: lowest button (nearest the left stick)",
    "left_middle": "Left group: middle button",
    "left_upper": "Left group: highest button",
    "right_lower": "Right group: lowest button (nearest the right stick)",
    "right_middle": "Right group: middle button",
    "right_upper": "Right group: highest button",
}


def draw_labelled_layout(surf, rect, theme, labels, active=None):
    """Action callouts attached to physical positions; no inferred wiring."""
    canvas = pygame.Surface((720, 480), pygame.SRCALPHA)
    draw_cabinet_layout(canvas, pygame.Rect(0, 90, 720, 300), theme)
    anchors = {
        "top_left": (95, 15), "top_middle_left": (270, 55),
        "top_middle_right": (450, 55), "top_right": (625, 15),
        "left_lower": (90, 440), "left_middle": (210, 402), "left_upper": (330, 440),
        "right_lower": (440, 440), "right_middle": (550, 402), "right_upper": (655, 440),
    }
    for position, (x, y) in BUTTON_POSITIONS.items():
        target = (x * 2, 90 + y * 2)
        ax, ay = anchors[position]
        colour = theme.c["warn"] if active == position else theme.c["accent2"]
        label = labels.get(position, "?")
        text = theme.text(label, 26, theme.c["text"], bold=True)
        box = text.get_rect(midtop=(ax, ay)).inflate(14, 8)
        anchor = (ax, box.bottom if position.startswith("top") else box.top)
        pygame.draw.line(canvas, colour, target, anchor, 3)
        pygame.draw.circle(canvas, colour, target, 21, 3)
        pygame.draw.rect(canvas, theme.c["panel_hi"], box, border_radius=7)
        pygame.draw.rect(canvas, colour, box, 2, border_radius=7)
        canvas.blit(text, text.get_rect(center=box.center))
    surf.blit(pygame.transform.smoothscale(canvas, rect.size), rect)


def draw_cabinet_layout(surf, rect, theme):
    # Supersample curved edges before reducing to the destination size.
    scale = 3
    panel = pygame.Surface((360 * scale, 150 * scale), pygame.SRCALPHA)

    def box(values):
        return tuple(v * scale for v in values)

    def circle(colour, centre, radius):
        pygame.draw.circle(panel, colour, box(centre), radius * scale)

    pygame.draw.rect(panel, (188, 192, 194), box((8, 5, 344, 137)), border_radius=30)
    pygame.draw.rect(panel, (225, 229, 231), box((8, 5, 344, 137)), 6, border_radius=30)

    def button(x, y, white=False, radius=9):
        circle((55, 59, 64), (x, y + 2), radius + 2)
        circle((239, 240, 242) if white else (20, 24, 29), (x, y), radius)
        pygame.draw.arc(panel, (255, 255, 255) if white else (76, 82, 91),
                        box((x - radius + 2, y - radius + 2, radius * 2 - 4, radius * 2 - 4)),
                        0.5, 2.7, 6)

    for x, white in [(142, True), (168, False), (190, False), (216, True)]:
        button(x, 23, white, 8)
    for x, y in [(88, 62), (105, 52), (122, 42), (270, 62), (287, 52), (304, 42)]:
        button(x, y)
    for x, y, white, name in [(57, 101, False, "8-way"), (180, 80, True, "Analogue"),
                               (255, 101, False, "8-way")]:
        circle((28, 31, 35), (x, y + 4), 15)
        button(x, y, white, 12)
        text = theme.text(name, 36, (20, 24, 29), bold=True)
        panel.blit(text, text.get_rect(midtop=box((x, y + 19))))
    surf.blit(pygame.transform.smoothscale(panel, rect.size), rect)
