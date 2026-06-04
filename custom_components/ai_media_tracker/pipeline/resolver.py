from .merge_state import merge_native_and_vlm

async def resolve_media(native_result, vlm_result):
    """
    Combines state results of both Native and VLM pipelines.
    Gracefully falls back if one pipeline fails or returns empty.
    """
    if vlm_result:
        return merge_native_and_vlm(native_result or {}, vlm_result)
    return native_result