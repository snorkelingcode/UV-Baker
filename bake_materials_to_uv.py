# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import os

import bpy
from bpy.types import Operator
from bpy.props import (
    EnumProperty,
    StringProperty,
)

bl_info = {
    "name": "Bake Materials to UV",
    "author": "Embody AI",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "3D Viewport > Object Context Menu",
    "description": "Bake PBR material channels (Color, Metallic, Roughness, Normal) to UV map then save image textures on disk",
    "category": "Object",
}

_image_enum_items = []
_uv_layer_enum_items = []


def get_image_items(self, context):
    global _image_enum_items
    items = []
    for img in bpy.data.images:
        w, h = img.size[0], img.size[1]
        # Skip images too small to be UV bake targets
        if w < 512 or h < 512:
            continue
        # Skip internal images
        if img.name in {'Render Result', 'Viewer Node'}:
            continue
        if img.name.startswith('_bake_'):
            continue
        # Skip thumbnails and asset browser junk
        name_lower = img.name.lower()
        if name_lower.startswith('thumbnail') or 'asset_type' in name_lower:
            continue
        label = f"{img.name} ({w}x{h})"
        items.append((img.name, label, ""))
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
    """Bake PBR material channels to UV-mapped image textures"""
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

        # Ensure object is selected (bake requires it)
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

        # Force GPU rendering for bake performance
        original_device = scene.cycles.device
        original_compute_type = None
        cycles_prefs = context.preferences.addons['cycles'].preferences
        original_compute_type = cycles_prefs.compute_device_type
        # Try GPU backends in order of preference
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
            # No GPU available, fall back to CPU
            scene.cycles.device = 'CPU'

        # Create one bake target image per map
        bake_images = {}
        for map_name in ('color', 'roughness', 'normal', 'metallic'):
            bake_images[map_name] = bpy.data.images.new(
                f"_bake_result_{map_name}", width=width, height=height, alpha=False,
            )

        wm = context.window_manager
        wm.progress_begin(0, 100)
        success = False

        try:
            # 1/4: Color
            temp_nodes = self._inject_bake_nodes(obj, bake_images['color'])
            bake = scene.render.bake
            orig_direct = bake.use_pass_direct
            orig_indirect = bake.use_pass_indirect
            orig_color = bake.use_pass_color
            bake.use_pass_direct = False
            bake.use_pass_indirect = False
            bake.use_pass_color = True
            wm.progress_update(0)
            bpy.ops.object.bake(type='DIFFUSE')
            bake.use_pass_direct = orig_direct
            bake.use_pass_indirect = orig_indirect
            bake.use_pass_color = orig_color
            self._remove_bake_nodes(temp_nodes)

            # 2/4: Roughness
            temp_nodes = self._inject_bake_nodes(obj, bake_images['roughness'])
            wm.progress_update(25)
            bpy.ops.object.bake(type='ROUGHNESS')
            self._remove_bake_nodes(temp_nodes)

            # 3/4: Normal
            temp_nodes = self._inject_bake_nodes(obj, bake_images['normal'])
            wm.progress_update(50)
            bpy.ops.object.bake(type='NORMAL')
            self._remove_bake_nodes(temp_nodes)

            # 4/4: Metallic (Emission rewire)
            temp_nodes = self._inject_bake_nodes(obj, bake_images['metallic'])
            restore_data = self._setup_metallic_rewire(obj)
            wm.progress_update(75)
            bpy.ops.object.bake(type='EMIT')
            self._restore_metallic_rewire(restore_data)
            self._remove_bake_nodes(temp_nodes)

            wm.progress_update(100)
            success = True

        except Exception as e:
            self.report({'ERROR'}, f"Bake failed: {e}")

        finally:
            wm.progress_end()
            # Restore original render device settings
            scene.cycles.device = original_device
            if original_compute_type is not None:
                cycles_prefs.compute_device_type = original_compute_type
            scene.render.engine = original_engine
            if obj.mode != original_mode:
                bpy.ops.object.mode_set(mode=original_mode)
            if not was_selected:
                obj.select_set(False)

        if not success:
            for img in bake_images.values():
                if img.name in [i.name for i in bpy.data.images]:
                    bpy.data.images.remove(img)
            return {'CANCELLED'}

        self.report({'INFO'}, "Bake complete! Choose where to save.")
        bpy.ops.object.bake_materials_save('INVOKE_DEFAULT')
        return {'FINISHED'}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

    def _setup_metallic_rewire(self, obj):
        restore_data = []
        for mat_slot in obj.material_slots:
            mat = mat_slot.material
            if mat is None or not mat.use_nodes or mat.node_tree is None:
                continue
            if mat.library is not None:
                continue

            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            principled = None
            for node in nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    principled = node
                    break
            if principled is None:
                continue

            metallic_input = principled.inputs["Metallic"]
            emission_color_input = principled.inputs["Emission Color"]
            emission_strength_input = principled.inputs["Emission Strength"]

            restore = {
                'material': mat,
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

            if metallic_input.is_linked:
                source = metallic_input.links[0].from_socket
                links.new(source, emission_color_input)
            else:
                val = metallic_input.default_value
                rgb_node = nodes.new('ShaderNodeRGB')
                rgb_node.name = "_bake_metallic_temp"
                rgb_node.outputs[0].default_value = (val, val, val, 1.0)
                links.new(rgb_node.outputs[0], emission_color_input)
                restore['temp_nodes'].append(rgb_node)

            emission_strength_input.default_value = 1.0
            restore_data.append(restore)
        return restore_data

    def _restore_metallic_rewire(self, restore_data):
        for restore in restore_data:
            mat = restore['material']
            principled = restore['principled']
            links = mat.node_tree.links
            nodes = mat.node_tree.nodes

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


class OBJECT_OT_bake_materials_save(Operator):
    """Save baked PBR textures to disk"""
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
        return bpy.data.images.get("_bake_result_color") is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        output_dir = bpy.path.abspath(self.directory)

        if not os.path.isdir(output_dir):
            self.report({'ERROR'}, "Output directory does not exist")
            return {'CANCELLED'}

        texture_map = {
            'color': '_bake_result_color',
            'metallic': '_bake_result_metallic',
            'roughness': '_bake_result_roughness',
            'normal': '_bake_result_normal',
        }

        saved = 0
        for map_name, img_name in texture_map.items():
            img = bpy.data.images.get(img_name)
            if img is not None:
                filepath = os.path.join(output_dir, f"{map_name}.png")
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

    for name in ['_bake_result_color', '_bake_result_metallic',
                 '_bake_result_roughness', '_bake_result_normal']:
        img = bpy.data.images.get(name)
        if img is not None:
            bpy.data.images.remove(img)


if __name__ == "__main__":
    register()
