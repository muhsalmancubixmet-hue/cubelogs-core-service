import os
from PIL import Image, ImageDraw

def create_pwa_icon(size, output_path):
    # Create RGBA image with transparent background
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. Draw rounded container background (squircle app icon style)
    # Background color: rich dark slate blue/navy
    bg_color_top = (15, 23, 42, 255) # #0f172a
    bg_color_bottom = (9, 15, 30, 255) # darker navy
    
    # Draw background squircle
    r = size // 5  # Corner radius
    draw.rounded_rectangle([0, 0, size, size], radius=r, fill=(15, 23, 42, 255))
    
    # 2. Draw 3D Isometric Cube in the center
    cx, cy = size // 2, size // 2
    
    # Scale cube dimensions based on icon size
    L = int(size * 0.22) # Cube side length
    h = int(L * 0.5)     # Projection height offset
    w = int(L * 0.866)   # Projection width offset
    
    # Top face vertices
    top_face = [
        (cx, cy - L),
        (cx + w, cy - h),
        (cx, cy),
        (cx - w, cy - h)
    ]
    # Left face vertices
    left_face = [
        (cx - w, cy - h),
        (cx, cy),
        (cx, cy + L),
        (cx - w, cy + L - h)
    ]
    # Right face vertices
    right_face = [
        (cx, cy),
        (cx + w, cy - h),
        (cx + w, cy + L - h),
        (cx, cy + L)
    ]
    
    # Draw cube faces with modern premium blue shading (light to dark)
    # Colors: bright blue, primary blue, dark blue
    draw.polygon(top_face, fill=(56, 189, 248, 255))   # Sky blue: #38bdf8
    draw.polygon(left_face, fill=(37, 99, 235, 255))   # Primary blue: #2563eb
    draw.polygon(right_face, fill=(29, 78, 216, 255))  # Darker blue: #1d4ed8
    
    # Add a glowing overlay / inner borders to make it look premium
    # Draw lines on the edges of the cube
    draw.line([top_face[0], top_face[1], top_face[2], top_face[3], top_face[0]], fill=(255, 255, 255, 80), width=size//100 + 1)
    draw.line([left_face[1], left_face[2], left_face[3]], fill=(255, 255, 255, 40), width=size//100 + 1)
    draw.line([right_face[1], right_face[2], right_face[3]], fill=(255, 255, 255, 40), width=size//100 + 1)
    
    # Save the generated image
    img.save(output_path, "PNG")
    print(f"Generated PWA icon: {output_path} ({size}x{size})")

if __name__ == "__main__":
    public_dir = r"c:\Users\user\Documents\project\fadi\1\cubelogs\frontend-app\public"
    os.makedirs(public_dir, exist_ok=True)
    
    create_pwa_icon(192, os.path.join(public_dir, "icon-192x192.png"))
    create_pwa_icon(512, os.path.join(public_dir, "icon-512x512.png"))
