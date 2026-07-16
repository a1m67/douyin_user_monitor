from __future__ import annotations

from typing import Any, Dict, List


def extract_image_urls(aweme_detail: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    urls.extend(_urls_from_image_list(aweme_detail.get("images", [])))
    image_post_info = aweme_detail.get("image_post_info", {})
    if not isinstance(image_post_info, dict):
        image_post_info = {}
    urls.extend(_urls_from_display_images(image_post_info.get("images", [])))
    return urls


def _urls_from_image_list(image_items: Any) -> List[str]:
    if not isinstance(image_items, list):
        return []
    urls: List[str] = []
    for image_item in image_items:
        if not isinstance(image_item, dict):
            continue
        url_list = image_item.get("url_list", [])
        if isinstance(url_list, list) and url_list:
            urls.append(url_list[0])
    return urls


def _urls_from_display_images(image_post_items: Any) -> List[str]:
    if not isinstance(image_post_items, list):
        return []
    urls: List[str] = []
    for image_item in image_post_items:
        if not isinstance(image_item, dict):
            continue
        display_image = image_item.get("display_image", {})
        if not isinstance(display_image, dict):
            continue
        url_list = display_image.get("url_list", [])
        if isinstance(url_list, list) and url_list:
            urls.append(url_list[0])
    return urls
