# Bake Materials to UV

A Blender add-on that bakes PBR material channels (Color, Metallic, Roughness, Normal) from Principled BSDF materials to image textures and saves them as PNGs.

## Requirements

- Blender 4.0 or later
- Object must have at least one material using the Principled BSDF shader
- Object must have a UV map
- At least one image must exist in the blend file (used to determine bake resolution)

## Installation

1. Open Blender and go to **Edit > Preferences > Add-ons**
2. Click **Install...** and select `bake_materials_to_uv.py`
3. Enable the add-on by checking the box next to **Bake Materials to UV**

## Usage

1. Select a mesh object in the 3D Viewport
2. Right-click to open the **Object Context Menu**
3. Click **Bake Materials to UV**
4. In the dialog that appears:
   - **UV Map** — choose which UV map to bake onto
   - **Target Image** — choose an existing image to determine the bake resolution (e.g., a 1024x1024 image will produce 1024x1024 textures)
5. Click **OK** to start baking
6. When baking completes, a file browser will open — choose a folder to save the output textures

## Output

Four PNG files are saved to your chosen directory:

| File | Description |
|------|-------------|
| `color.png` | Base color / albedo |
| `metallic.png` | Metallic map |
| `roughness.png` | Roughness map |
| `normal.png` | Normal map |

## Notes

- The add-on temporarily switches the render engine to Cycles for baking, then restores your original engine when done
- Metallic is baked by routing the metallic value through the emission channel, so the bake captures the correct data even without a native metallic bake type
- Linked/library materials are skipped during baking
- The bake resolution is determined by the image you select in the dialog — it does not modify that image, it only reads its dimensions

## License

GPL-2.0-or-later
