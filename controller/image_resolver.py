from __future__ import annotations


CLUSTER_REGISTRIES = {
    "vm1-cluster": "host.docker.internal:5000",
    "vm2-cluster": "host.docker.internal:5001",
}


class ImageResolutionError(RuntimeError):
    pass


def resolve_cluster_image(
    cluster_name: str,
    image: str,
) -> str:
    registry = CLUSTER_REGISTRIES.get(cluster_name)

    if registry is None:
        raise ImageResolutionError(
            f"No registry configured for cluster {cluster_name}"
        )

    image = image.strip()

    if not image:
        raise ImageResolutionError(
            "Image must not be empty"
        )

    # If the developer already sent a registry-qualified image,
    # keep it as-is for now.
    first_part = image.split("/", 1)[0]

    if "." in first_part or ":" in first_part or first_part == "localhost":
        return image

    return f"{registry}/{image}"
