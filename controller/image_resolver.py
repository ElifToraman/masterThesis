from __future__ import annotations


class ImageResolutionError(RuntimeError):
    pass


def resolve_image_for_registry(
    *,
    image: str,
    registry: str,
) -> str:
    image = image.strip()
    registry = registry.strip().rstrip("/")

    if not image:
        raise ImageResolutionError(
            "Image must not be empty"
        )

    if not registry:
        raise ImageResolutionError(
            "Registry must not be empty"
        )

    image_without_registry = image

    if "/" in image:
        first_part, remainder = image.split("/", maxsplit=1)

        if (
            "." in first_part
            or ":" in first_part
            or first_part == "localhost"
        ):
            image_without_registry = remainder

    return f"{registry}/{image_without_registry}"
