"""Two small ComfyUI nodes: keep an encoded prompt on disk, so the text encoder and the video model
never have to be in memory in the same run (16 GB Mac). Files: ComfyUI's output/seekaas_cond/<key>.pt"""
import os
import re

import folder_paths
import torch


def _path(key: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{8,64}", key):
        raise ValueError("key must be a hash")
    d = os.path.join(folder_paths.get_output_directory(), "seekaas_cond")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{key}.pt")


class SeekaasSaveConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"conditioning": ("CONDITIONING",), "key": ("STRING", {"default": ""})}}

    RETURN_TYPES = ()
    OUTPUT_NODE = True
    FUNCTION = "save"
    CATEGORY = "seekaas"

    def save(self, conditioning, key):
        rows = []
        for cond, extra in conditioning:
            keep = {k: (v.detach().cpu() if torch.is_tensor(v) else v) for k, v in extra.items()
                    if torch.is_tensor(v) or isinstance(v, (int, float, str, bool, type(None)))}
            rows.append([cond.detach().cpu(), keep])
        path = _path(key)
        torch.save(rows, path + ".part")
        os.replace(path + ".part", path)
        return {}


class SeekaasLoadConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"key": ("STRING", {"default": ""})}}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "load"
    CATEGORY = "seekaas"

    def load(self, key):
        return ([list(r) for r in torch.load(_path(key), map_location="cpu", weights_only=True)],)


NODE_CLASS_MAPPINGS = {"SeekaasSaveConditioning": SeekaasSaveConditioning, "SeekaasLoadConditioning": SeekaasLoadConditioning}
