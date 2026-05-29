# Bake Materials to UV

A Blender add-on that bakes PBR materials into UE5-ready textures. Produces an optimized texture set with ORM channel packing, matching the standard Substance Painter to Unreal Engine workflow.

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

## Notes

- The add-on temporarily switches to Cycles and uses GPU rendering for baking, then restores your original settings
- All materials on the object are baked onto a single UV map (same as Substance Painter export)
- Library-linked materials are temporarily made local for baking, then restored
- The ORM texture packs three grayscale maps into one RGB image, reducing texture memory by 66%
- Metallic and Opacity are baked by routing their values through the Emission channel since Blender has no native bake type for these
- Emissive is baked before Opacity and Metallic to capture the original emission data before any rewiring
- The bake resolution is determined by the image you select in the dialog — it does not modify that image

## License

GPL-2.0-or-later
