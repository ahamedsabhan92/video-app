"""Which images a project needs, and how each export is framed.

- Main aspect: native images in assets/images/<shot>.png        (data["images"])
- Second aspect, mode "native": assets/images/<9x16>/<shot>.png  (data["images_second"])
- Second aspect, mode "blurpad": no extra images; render fits the main image over a blurred copy.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.store.projects import Project


@dataclass(frozen=True)
class ImageSet:
    aspect: str
    key: str        # project.data key holding per-shot metadata
    subdir: str     # under assets/images/ ("" for the main set)

    def rel_path(self, shot_id: str) -> str:
        return f"assets/images/{self.subdir + '/' if self.subdir else ''}{shot_id}.png"


@dataclass(frozen=True)
class ExportPlan:
    aspect: str
    images: ImageSet   # which images feed this export
    framing: str       # "cover" (native images) | "blurpad"


def tag(aspect: str) -> str:
    return aspect.replace(":", "x")


def image_sets(p: Project) -> list[ImageSet]:
    sets = [ImageSet(p.aspect, "images", "")]
    if p.second_aspect and p.second_aspect_mode == "native":
        sets.append(ImageSet(p.second_aspect, "images_second", tag(p.second_aspect)))
    return sets


def export_plans(p: Project) -> list[ExportPlan]:
    main = ImageSet(p.aspect, "images", "")
    plans = [ExportPlan(p.aspect, main, "cover")]
    if p.second_aspect:
        if p.second_aspect_mode == "native":
            plans.append(ExportPlan(p.second_aspect, image_sets(p)[1], "cover"))
        else:
            plans.append(ExportPlan(p.second_aspect, main, "blurpad"))
    return plans
