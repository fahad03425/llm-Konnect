import os
from PIL import Image, ImageDraw, ImageFilter
import math

def create_ai_icon(size=512):
    # Create RGBA image
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    center = size / 2
    radius = size * 0.44
    
    # Outer ambient glow
    glow = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    
    # Rounded squircle badge background with glass gradient
    corner_radius = int(size * 0.22)
    padding = int(size * 0.05)
    
    # Draw dark glass rounded squircle
    squircle_box = [padding, padding, size - padding, size - padding]
    
    # Background gradient for badge
    badge = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    badge_draw = ImageDraw.Draw(badge)
    
    badge_draw.rounded_rectangle(squircle_box, radius=corner_radius, fill=(15, 23, 42, 245), outline=(51, 65, 85, 255), width=max(2, int(size*0.012)))
    
    # Draw glowing inner accent ring
    inner_box = [padding + 6, padding + 6, size - padding - 6, size - padding - 6]
    badge_draw.rounded_rectangle(inner_box, radius=corner_radius - 4, outline=(16, 185, 129, 60), width=max(1, int(size*0.006)))

    # Composite badge onto img
    img = Image.alpha_composite(img, badge)
    
    # Now draw the Futuristic AI Lightning & Neural Core in center
    # Main high-tech polygon lightning / AI node coordinates
    icon_layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    icon_draw = ImageDraw.Draw(icon_layer)
    
    # Lightning / Prism vertices (centered and balanced)
    s = size / 512.0
    
    # Outer glow points for lightning
    poly_pts = [
        (275 * s, 68 * s),
        (135 * s, 260 * s),
        (235 * s, 260 * s),
        (185 * s, 444 * s),
        (375 * s, 220 * s),
        (275 * s, 220 * s),
        (345 * s, 68 * s)
    ]
    
    # Cyan/Emerald to Violet gradient simulation with layers
    # Outer shadow/glow
    glow_poly = [(p[0], p[1]) for p in poly_pts]
    glow_draw.polygon(glow_poly, fill=(16, 185, 129, 180))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=int(18 * s)))
    img = Image.alpha_composite(img, glow)
    
    # Main polygon
    icon_draw.polygon(poly_pts, fill=(16, 185, 129, 255))
    
    # Inner dynamic facets for 3D/AI crystal feel
    # Top facet (cyan/blue)
    facet_top = [
        (275 * s, 68 * s),
        (135 * s, 260 * s),
        (235 * s, 260 * s),
        (255 * s, 175 * s)
    ]
    icon_draw.polygon(facet_top, fill=(6, 182, 212, 240))
    
    # Bottom facet (emerald / violet)
    facet_bot = [
        (235 * s, 260 * s),
        (185 * s, 444 * s),
        (375 * s, 220 * s),
        (275 * s, 220 * s)
    ]
    icon_draw.polygon(facet_bot, fill=(16, 185, 129, 255))
    
    # Center neural energy streak
    facet_core = [
        (275 * s, 68 * s),
        (255 * s, 175 * s),
        (375 * s, 220 * s),
        (345 * s, 68 * s)
    ]
    icon_draw.polygon(facet_core, fill=(139, 92, 246, 230))
    
    # Highlight lines
    icon_draw.line([(275 * s, 68 * s), (135 * s, 260 * s)], fill=(255, 255, 255, 220), width=max(1, int(3*s)))
    icon_draw.line([(235 * s, 260 * s), (185 * s, 444 * s)], fill=(255, 255, 255, 220), width=max(1, int(3*s)))
    icon_draw.line([(375 * s, 220 * s), (345 * s, 68 * s)], fill=(255, 255, 255, 180), width=max(1, int(2*s)))

    # Neural Sparkle Dots / AI nodes
    nodes = [
        (120 * s, 150 * s, 8 * s, (6, 182, 212, 230)),
        (395 * s, 140 * s, 10 * s, (139, 92, 246, 240)),
        (115 * s, 360 * s, 9 * s, (16, 185, 129, 230)),
        (390 * s, 350 * s, 11 * s, (6, 182, 212, 240)),
    ]
    for x, y, r, c in nodes:
        icon_draw.ellipse([x - r, y - r, x + r, y + r], fill=c)
        icon_draw.ellipse([x - r*0.4, y - r*0.4, x + r*0.4, y + r*0.4], fill=(255, 255, 255, 255))
        
    img = Image.alpha_composite(img, icon_layer)
    return img

def main():
    base_dir = r"c:\Users\User\Downloads\llm-Konnect (3)\llm-Konnect\desktop"
    tauri_icons = os.path.join(base_dir, "src-tauri", "icons")
    public_dir = os.path.join(base_dir, "public")
    
    os.makedirs(tauri_icons, exist_ok=True)
    os.makedirs(public_dir, exist_ok=True)
    
    icon_512 = create_ai_icon(512)
    icon_512.save(os.path.join(tauri_icons, "icon.png"), "PNG")
    
    sizes = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "Square30x30Logo.png": 30,
        "Square44x44Logo.png": 44,
        "Square71x71Logo.png": 71,
        "Square89x89Logo.png": 89,
        "Square107x107Logo.png": 107,
        "Square142x142Logo.png": 142,
        "Square150x150Logo.png": 150,
        "Square284x284Logo.png": 284,
        "Square310x310Logo.png": 310,
        "StoreLogo.png": 50,
    }
    
    for filename, sz in sizes.items():
        resized = icon_512.resize((sz, sz), Image.Resampling.LANCZOS)
        resized.save(os.path.join(tauri_icons, filename), "PNG")
        
    # Generate icon.ico containing multiple resolutions (16, 32, 48, 64, 128, 256)
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon_512.save(os.path.join(tauri_icons, "icon.ico"), format="ICO", sizes=ico_sizes)
    
    print("All Tauri icons generated successfully!")

if __name__ == "__main__":
    main()
