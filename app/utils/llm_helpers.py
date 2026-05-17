def safe_extract_text(response) -> str:
    """
    Safely extracts text from an LLM response object or raw string.
    Prevents 'str' object has no attribute 'text' errors.
    """
    if response is None:
        return ""
    if hasattr(response, 'text'):
        return response.text if response.text else ""
    return str(response)
