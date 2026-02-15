# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import os

import numpy as np

import bpy
from bpy.types import Operator
from bpy.props import (
    EnumProperty,
    StringProperty,
)

bl_info = {
    "name": "Bake Materials to UV",
    "author": "Embody AI",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "3D Viewport > Object Context Menu",
    "description": "Bake PBR materials to UE5-ready textures (BaseColor, Normal, ORM)",
    "category": "Object",
}

_image_enum_items = []
_uv_layer_enum_items = []


def get_image_items(self, context):
    global _image_enum_items
    items = []
    for img in bpy.data.images:
        w, h = img.size[0], img.size[1]
        if w < 512 or h < 512:
            continue
        if img.name in {'Render Result', 'Viewer Node'}:
            continue
        if img.name.startswith('_bake_'):
            continue
        name_lower = img.name.lower()
        if name_lower.startswith('thumbnail') or 'asset_type' in name_lower:
            continue
        label = f"{img.name} ({w}x{h})"
        items.append((img.name, label, ""))
    items.reverse()
    if not items:
        items = [('NONE', 'No images available', '')]
    _image_enum_items = items
    return items


def get_uv_layer_items(self, context):
    global _uv_layer_enum_items
    items = []
    obj = context.active_object
    if obj and obj.type == 'MESH':
        for uv_layer in obj.data.uv_layers:
            items.append((uv_layer.name, uv_layer.name, ""))
    if not items:
        items = [('NONE', 'No UV maps available', '')]
    _uv_layer_enum_items = items
    return items


class OBJECT_OT_bake_materials_to_uv(Operator):
    """Bake PBR materials to UE5-ready textures (BaseColor, Normal, ORM)"""
    bl_idname = "object.bake_materials_to_uv"
    bl_label = "Bake Materials to UV"
    bl_options = {'REGISTER', 'UNDO'}

    target_image: EnumProperty(
        name="Target Image",
        description="Image that determines bake resolution",
        items=get_image_items,
    )

    target_uv_layer: EnumProperty(
        name="UV Map",
        description="UV map to use for baking",
        items=get_uv_layer_items,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and obj.data.uv_layers.active is not None
            and len(obj.data.materials) > 0
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "target_uv_layer")
        layout.prop(self, "target_image")

    def execute(self, context):
        obj = context.active_object
        scene = context.scene

        if self.target_image == 'NONE':
            self.report({'ERROR'}, "No valid image selected")
            return {'CANCELLED'}

        target_img = bpy.data.images.get(self.target_image)
        if target_img is None:
            self.report({'ERROR'}, f"Image '{self.target_image}' not found")
            return {'CANCELLED'}

        width, height = target_img.size[0], target_img.size[1]

        # Set active UV layer
        if self.target_uv_layer != 'NONE':
            uv_layer = obj.data.uv_layers.get(self.target_uv_layer)
            if uv_layer:
                obj.data.uv_layers.active = uv_layer

        # Ensure object is selected
        was_selected = obj.select_get()
        obj.select_set(True)

        # Ensure Object Mode
        original_mode = obj.mode
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Switch to Cycles
        original_engine = scene.render.engine
        if original_engine != 'CYCLES':
            scene.render.engine = 'CYCLES'

        # Force GPU rendering
        original_device = scene.cycles.device
        cycles_prefs = context.preferences.addons['cycles'].preferences
        original_compute_type = cycles_prefs.compute_device_type
        gpu_activated = False
        for backend in ('CUDA', 'OPTIX', 'HIP', 'ONEAPI', 'METAL'):
            if backend in {t[0] for t in cycles_prefs.get_device_types(context)}:
                cycles_prefs.compute_device_type = backend
                cycles_prefs.get_devices()
                for device in cycles_prefs.devices:
                    device.use = True
                scene.cycles.device = 'GPU'
                gpu_activated = True
                break
        if not gpu_activated:
            scene.cycles.device = 'CPU'

        # Make library-linked materials local so we can inject bake nodes
        original_materials = self._make_materials_local(obj)

        # Create bake target images
        bake_images = {}
        for ch in ('basecolor', 'normal', 'ao', 'roughness', 'metallic',
                    'emissive', 'opacity'):
            bake_images[ch] = bpy.data.images.new(
                f"_bake_{ch}", width=width, height=height, alpha=False,
            )

        wm = context.window_manager
        wm.progress_begin(0, 7)
        success = False

        try:
            bake_settings = scene.render.bake

            # 1/7: Emissive (must bake real emission before any rewires)
            temp_nodes = self._inject_bake_nodes(obj, bake_images['emissive'])
            wm.progress_update(0)
            bpy.ops.object.bake(type='EMIT')
            self._remove_bake_nodes(temp_nodes)

            # 2/7: Base Color (via Emission rewire — captures raw color
            #       regardless of transmission/alpha)
            temp_nodes = self._inject_bake_nodes(obj, bake_images['basecolor'])
            restore_data = self._setup_rewire(obj, "Base Color")
            wm.progress_update(1)
            bpy.ops.object.bake(type='EMIT')
            self._restore_rewire(restore_data)
            self._remove_bake_nodes(temp_nodes)

            # 3/7: Normal
            temp_nodes = self._inject_bake_nodes(obj, bake_images['normal'])
            wm.progress_update(2)
            bpy.ops.object.bake(type='NORMAL')
            self._remove_bake_nodes(temp_nodes)

            # 4/7: AO
            temp_nodes = self._inject_bake_nodes(obj, bake_images['ao'])
            wm.progress_update(3)
            bpy.ops.object.bake(type='AO')
            self._remove_bake_nodes(temp_nodes)

            # 5/7: Roughness
            temp_nodes = self._inject_bake_nodes(obj, bake_images['roughness'])
            wm.progress_update(4)
            bpy.ops.object.bake(type='ROUGHNESS')
            self._remove_bake_nodes(temp_nodes)

            # 6/7: Opacity (Alpha → Emission rewire)
            temp_nodes = self._inject_bake_nodes(obj, bake_images['opacity'])
            restore_data = self._setup_rewire(obj, "Alpha")
            wm.progress_update(5)
            bpy.ops.object.bake(type='EMIT')
            self._restore_rewire(restore_data)
            self._remove_bake_nodes(temp_nodes)

            # 7/7: Metallic (Metallic → Emission rewire)
            temp_nodes = self._inject_bake_nodes(obj, bake_images['metallic'])
            restore_data = self._setup_rewire(obj, "Metallic")
            wm.progress_update(6)
            bpy.ops.object.bake(type='EMIT')
            self._restore_rewire(restore_data)
            self._remove_bake_nodes(temp_nodes)

            # Pack ORM texture: AO(R) + Roughness(G) + Metallic(B)
            orm_img = self._pack_orm(
                bake_images['ao'], bake_images['roughness'],
                bake_images['metallic'], width, height,
            )
            bake_images['orm'] = orm_img

            wm.progress_update(7)
            success = True

        except Exception as e:
            self.report({'ERROR'}, f"Bake failed: {e}")

        finally:
            wm.progress_end()
            self._restore_original_materials(obj, original_materials)
            scene.cycles.device = original_device
            cycles_prefs.compute_device_type = original_compute_type
            scene.render.engine = original_engine
            if obj.mode != original_mode:
                bpy.ops.object.mode_set(mode=original_mode)
            if not was_selected:
                obj.select_set(False)

        # Clean up intermediate images (AO, Roughness, Metallic) — only ORM is saved
        for ch in ('ao', 'roughness', 'metallic'):
            img = bake_images.get(ch)
            if img and img.name in [i.name for i in bpy.data.images]:
                bpy.data.images.remove(img)

        if not success:
            for img in bake_images.values():
                if img.name in [i.name for i in bpy.data.images]:
                    bpy.data.images.remove(img)
            return {'CANCELLED'}

        scene["_bake_target_image_name"] = self.target_image
        self.report({'INFO'}, "Bake complete! Choose where to save.")
        bpy.ops.object.bake_materials_save('INVOKE_DEFAULT')
        return {'FINISHED'}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_materials_local(self, obj):
        original_materials = {}
        for idx, mat_slot in enumerate(obj.material_slots):
            mat = mat_slot.material
            if mat is not None and mat.library is not None:
                local_mat = mat.copy()
                original_materials[idx] = mat
                obj.material_slots[idx].material = local_mat
        return original_materials

    def _restore_original_materials(self, obj, original_materials):
        for idx, original_mat in original_materials.items():
            local_mat = obj.material_slots[idx].material
            obj.material_slots[idx].material = original_mat
            if local_mat is not None and local_mat.users == 0:
                bpy.data.materials.remove(local_mat)

    def _inject_bake_nodes(self, obj, bake_image):
        temp_nodes = []
        for mat_slot in obj.material_slots:
            mat = mat_slot.material
            if mat is None or not mat.use_nodes or mat.node_tree is None:
                continue
            if mat.library is not None:
                continue

            nodes = mat.node_tree.nodes
            for n in nodes:
                n.select = False

            bake_node = nodes.new('ShaderNodeTexImage')
            bake_node.name = "_bake_temp_node"
            bake_node.image = bake_image
            bake_node.select = True
            nodes.active = bake_node

            temp_nodes.append((mat, bake_node))
        return temp_nodes

    def _remove_bake_nodes(self, temp_nodes):
        for mat, node in temp_nodes:
            if mat.node_tree is not None:
                mat.node_tree.nodes.remove(node)

    def _find_principled(self, node_tree, depth=0):
        """Find Principled BSDF and its containing node_tree, searching inside groups."""
        if depth > 10:
            return None, None
        for node in node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                return node, node_tree
            if node.type == 'GROUP' and node.node_tree is not None:
                result, result_tree = self._find_principled(node.node_tree, depth + 1)
                if result is not None:
                    return result, result_tree
        return None, None

    def _setup_rewire(self, obj, source_input_name):
        """Route a Principled BSDF input (e.g. 'Metallic', 'Alpha') to Emission for baking."""
        restore_data = []
        for mat_slot in obj.material_slots:
            mat = mat_slot.material
            if mat is None or not mat.use_nodes or mat.node_tree is None:
                continue
            if mat.library is not None:
                continue

            principled, containing_tree = self._find_principled(mat.node_tree)
            if principled is None:
                continue

            nodes = containing_tree.nodes
            links = containing_tree.links

            source_input = principled.inputs[source_input_name]
            emission_color_input = principled.inputs["Emission Color"]
            emission_strength_input = principled.inputs["Emission Strength"]

            restore = {
                'node_tree': containing_tree,
                'principled': principled,
                'emission_color_links': [
                    link.from_socket for link in emission_color_input.links
                ],
                'emission_color_default': list(emission_color_input.default_value),
                'emission_strength_value': emission_strength_input.default_value,
                'temp_nodes': [],
            }

            for link in list(emission_color_input.links):
                links.remove(link)

            if source_input.is_linked:
                source = source_input.links[0].from_socket
                links.new(source, emission_color_input)
            else:
                val = source_input.default_value
                rgb_node = nodes.new('ShaderNodeRGB')
                rgb_node.name = f"_bake_{source_input_name}_temp"
                # Handle both color (RGBA) and scalar inputs
                if hasattr(val, '__len__'):
                    rgb_node.outputs[0].default_value = (val[0], val[1], val[2], 1.0)
                else:
                    rgb_node.outputs[0].default_value = (val, val, val, 1.0)
                links.new(rgb_node.outputs[0], emission_color_input)
                restore['temp_nodes'].append(rgb_node)

            emission_strength_input.default_value = 1.0
            restore_data.append(restore)
        return restore_data

    def _restore_rewire(self, restore_data):
        for restore in restore_data:
            containing_tree = restore['node_tree']
            principled = restore['principled']
            links = containing_tree.links
            nodes = containing_tree.nodes

            emission_color_input = principled.inputs["Emission Color"]
            emission_strength_input = principled.inputs["Emission Strength"]

            for link in list(emission_color_input.links):
                links.remove(link)
            for from_socket in restore['emission_color_links']:
                links.new(from_socket, emission_color_input)

            emission_color_input.default_value = restore['emission_color_default']
            emission_strength_input.default_value = restore['emission_strength_value']

            for temp_node in restore['temp_nodes']:
                nodes.remove(temp_node)

    def _pack_orm(self, ao_img, rough_img, metal_img, width, height):
        """Pack AO(R), Roughness(G), Metallic(B) into a single ORM texture."""
        ao = np.array(ao_img.pixels[:]).reshape(-1, 4)
        rough = np.array(rough_img.pixels[:]).reshape(-1, 4)
        metal = np.array(metal_img.pixels[:]).reshape(-1, 4)

        orm = np.ones((width * height, 4), dtype=np.float32)
        orm[:, 0] = ao[:, 0]       # R = Ambient Occlusion
        orm[:, 1] = rough[:, 0]    # G = Roughness
        orm[:, 2] = metal[:, 0]    # B = Metallic
        orm[:, 3] = 1.0            # A = 1.0

        orm_img = bpy.data.images.new("_bake_orm", width=width, height=height, alpha=False)
        orm_img.pixels = orm.flatten().tolist()
        return orm_img


class OBJECT_OT_bake_materials_save(Operator):
    """Save baked UE5 textures to disk"""
    bl_idname = "object.bake_materials_save"
    bl_label = "Save Baked Textures"
    bl_options = {'REGISTER'}

    directory: StringProperty(
        name="Output Directory",
        description="Directory to save baked textures",
        subtype='DIR_PATH',
    )

    @classmethod
    def poll(cls, context):
        return bpy.data.images.get("_bake_basecolor") is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        output_dir = bpy.path.abspath(self.directory)

        if not os.path.isdir(output_dir):
            self.report({'ERROR'}, "Output directory does not exist")
            return {'CANCELLED'}

        target_name = context.scene.get("_bake_target_image_name", "Texture")

        texture_map = {
            'BC': '_bake_basecolor',
            'N': '_bake_normal',
            'ORM': '_bake_orm',
            'E': '_bake_emissive',
            'O': '_bake_opacity',
        }

        saved = 0
        for suffix, img_name in texture_map.items():
            img = bpy.data.images.get(img_name)
            if img is not None:
                filepath = os.path.join(output_dir, f"T_{target_name}_{suffix}.png")
                img.filepath_raw = filepath
                img.file_format = 'PNG'
                img.save()
                bpy.data.images.remove(img)
                saved += 1

        if saved == 0:
            self.report({'ERROR'}, "No baked textures found to save")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Saved {saved} texture(s) to {output_dir}")
        return {'FINISHED'}


def menu_func(self, context):
    if context.active_object and context.active_object.type == 'MESH':
        self.layout.separator()
        self.layout.operator_context = 'INVOKE_DEFAULT'
        self.layout.operator(OBJECT_OT_bake_materials_to_uv.bl_idname)


def register():
    bpy.utils.register_class(OBJECT_OT_bake_materials_to_uv)
    bpy.utils.register_class(OBJECT_OT_bake_materials_save)
    bpy.types.VIEW3D_MT_object_context_menu.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_object_context_menu.remove(menu_func)
    bpy.utils.unregister_class(OBJECT_OT_bake_materials_save)
    bpy.utils.unregister_class(OBJECT_OT_bake_materials_to_uv)

    for img in list(bpy.data.images):
        if img.name.startswith('_bake_'):
            bpy.data.images.remove(img)


if __name__ == "__main__":
    register()
