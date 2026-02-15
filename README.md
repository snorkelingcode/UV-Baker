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

## Output

Five PNG files are saved using UE5 naming conventions:

| File | Description |
|------|-------------|
| `T_{Name}_BC.png` | Base Color (albedo) |
| `T_{Name}_N.png` | Normal map (tangent space) |
| `T_{Name}_ORM.png` | Packed: Ambient Occlusion (R), Roughness (G), Metallic (B) |
| `T_{Name}_E.png` | Emissive color |
| `T_{Name}_O.png` | Opacity (alpha) |

## Importing into Unreal Engine 5

### Opaque materials (wood, plastic, metal, etc.)

1. Import `T_{Name}_BC`, `T_{Name}_N`, and `T_{Name}_ORM`
2. Create a new Material, set **Blend Mode** to **Opaque** (default)
3. Connect:
   - `T_{Name}_BC` to **Base Color**
   - `T_{Name}_N` to **Normal**
   - `T_{Name}_ORM` Red channel to **Ambient Occlusion**
   - `T_{Name}_ORM` Green channel to **Roughness**
   - `T_{Name}_ORM` Blue channel to **Metallic**
4. Set the ORM and Normal textures to **Linear Color** (not sRGB) in the texture asset settings

**Tip:** Use a single `TextureSampleParameter2D` for the ORM texture and split channels with a **BreakOutFloat3Components** node (or just use the R/G/B output pins directly).

### Translucent / glass materials

1. Follow the Opaque steps above, then also import `T_{Name}_O`
2. Change **Blend Mode** to **Translucent** (or **Masked** for binary cutouts)
3. Connect `T_{Name}_O` to the **Opacity** input
4. Under **Material > Translucency**, set **Lighting Mode** to **Surface ForwardShading** for best glass results

### Emissive materials (lights, LEDs, screens)

1. Follow the Opaque steps above, then also import `T_{Name}_E`
2. Connect `T_{Name}_E` to the **Emissive Color** input
3. To increase glow intensity, multiply the emissive texture by a scalar parameter before connecting
4. Enable **Bloom** in Post Process settings to see the glow effect in-game

### Which textures do I need?

| UE5 Material Type | Required Textures |
|---|---|
| Opaque (default) | BC, N, ORM |
| Opaque + Emissive | BC, N, ORM, E |
| Translucent / Glass | BC, N, ORM, O |
| Masked (cutouts) | BC, N, ORM, O |
| Translucent + Emissive | BC, N, ORM, E, O |

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
