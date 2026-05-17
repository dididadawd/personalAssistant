import os
import json
import ast
import time
import logging
from typing import List, Optional, Any, Union

from google import genai
from google.genai import types

from json_repair import repair_json

from ..config import Config

logger = logging.getLogger(__name__)

# --- Configuration ---
GEMINI_MODEL_NAME = Config.GEMINI_MODEL_NAME
GEMINI_MODEL_FOR_COMPLEX_NAME = Config.GEMINI_MODEL_FOR_COMPLEX_NAME
GEMINI_VISION_MODEL = Config.GEMINI_VISION_MODEL

# Load single API key from Config
GOOGLE_GEMINI_API_KEY = Config.GOOGLE_GEMINI_API_KEY
if not GOOGLE_GEMINI_API_KEY:
    raise ValueError("No Google Gemini API key found. This service costs money to run - a valid paid API key is required.")

# Create single client
client = genai.Client(api_key=GOOGLE_GEMINI_API_KEY)

# --- Compatibility classes (kept for legacy usage if needed) ---
class SimpleResponse:
    """Wrapper mimicking Google Generative AI response structure."""
    def __init__(self, text: str):
        self.text = text
    
    @property
    def candidates(self):
        class Part:
            def __init__(self, t): self.text = t
        class Content:
            def __init__(self, t): self.parts = [Part(t)]
        class Candidate:
            def __init__(self, t): 
                self.content = Content(t)
                self.finish_reason = type('FinishReason', (), {'name': "STOP"})()
        return [Candidate(self.text)]

class SimpleEmbedding:
    """Wrapper mimicking embedding response."""
    def __init__(self, embedding: List[float]):
        self.values = embedding

# --- API Manager (single key) ---
class ApiManager:
    """
    Manages a single Google API key for all LLM calls.
    Note: This service costs money to run. A valid paid API key is required for better performance.
    """
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("ApiManager requires a valid API key.")
        self.api_key = api_key
        self.client = genai.Client(api_key=api_key)

    def generate_content(self, contents: Any, **kwargs):
        """
        Calls Gemini's generate_content using the new genai SDK.
        Automatically enforces thinking_level="high".
        Pass model_name=... as a kwarg to select a specific model.
        Includes retry logic for transient errors (500, 502, 503, 504).
        """
        model_name = kwargs.pop('model_name', GEMINI_MODEL_NAME)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_level="high")
                    ),
                )
                return response.text
            except Exception as e:
                error_str = str(e)
                is_transient_error = any(code in error_str for code in ["500", "502", "503", "504"])
                
                if is_transient_error and attempt < max_retries - 1:
                    wait_time = 2 ** (attempt + 1)
                    logger.warning(f"Transient API error (attempt {attempt+1}/{max_retries}). Retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
                else:
                    raise

    def embed_content(self, contents: Any, **kwargs):
        """
        Calls Gemini's embed_content using the new genai SDK.
        """
        model_name = kwargs.pop('model_name', Config.EMBEDDING_MODEL)
        
        return self.client.models.embed_content(
            model=model_name,
            contents=contents,
            **kwargs
        )

    def safe_generate_content(self, prompt: str, model_name: str = GEMINI_MODEL_NAME, max_retries: int = 5, backoff_factor: int = 3):
        """
        Calls generate_content with full failsafe handling:
        - 429 rate limit retries with exponential backoff
        - Response structure validation (safety filters, finish reasons, empty content)
        - JSON extraction from raw text (first '{' to last '}')
        - JSON parsing fallback chain: json.loads -> repair_json -> ast.literal_eval -> raw text fallback

        Returns dict:
            {"success": bool, "decision": parsed_dict_or_none, "raw_text": str, "error": str_or_none}
        """
        # --- Step 1: Retry with exponential backoff for 429 rate limits ---
        reasoning_response = None
        for i in range(max_retries):
            try:
                reasoning_response = self.generate_content(prompt, model_name=model_name)
                break
            except Exception as e:
                error_str = str(e)
                is_retryable = "429" in error_str or any(code in error_str for code in ["500", "502", "503"])
                if is_retryable:
                    if i == max_retries - 1:
                        return {"success": False, "decision": None, "raw_text": "", "error": f"API error exceeded {max_retries} retries: {e}"}
                    wait_time = backoff_factor * (i + 1)
                    logger.warning(f"API rate limit or server error hit. Retrying in {wait_time} seconds... (Attempt {i+1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    return {"success": False, "decision": None, "raw_text": "", "error": f"Unexpected API error: {e}"}

        if reasoning_response is None:
            return {"success": False, "decision": None, "raw_text": "", "error": "No response received from API"}

        # --- Step 2: Validate response structure ---
        raw_text_from_ai = ""
        if isinstance(reasoning_response, str):
            raw_text_from_ai = reasoning_response
        elif hasattr(reasoning_response, 'text'):
            raw_text_from_ai = reasoning_response.text
        elif hasattr(reasoning_response, 'candidates') and reasoning_response.candidates:
            candidate = reasoning_response.candidates[0]
            if hasattr(candidate, 'finish_reason') and candidate.finish_reason.name != "STOP":
                finish_reason_info = f"Finish Reason: {candidate.finish_reason.name}"
                if candidate.finish_reason.name == "SAFETY":
                    finish_reason_info += f", Safety Ratings: {candidate.safety_ratings}"
                return {"success": False, "decision": None, "raw_text": "", "error": f"AI response invalid: {finish_reason_info}"}
            if not candidate.content.parts:
                return {"success": False, "decision": None, "raw_text": "", "error": "AI returned no content parts"}
            raw_text_from_ai = candidate.content.parts[0].text
        else:
            return {"success": False, "decision": None, "raw_text": "", "error": "AI response empty or unrecognized format"}

        raw_text_from_ai = raw_text_from_ai.strip()
        if not raw_text_from_ai:
            return {"success": False, "decision": None, "raw_text": "", "error": "AI returned empty text"}

        # --- Step 3: Extract JSON from raw text ---
        start_index = raw_text_from_ai.find('{')
        end_index = raw_text_from_ai.rfind('}')
        if start_index != -1 and end_index != -1 and end_index > start_index:
            decision_text = raw_text_from_ai[start_index : end_index + 1]
        else:
            decision_text = raw_text_from_ai

        # --- Step 4: Parse JSON with fallback chain ---
        decision = None
        try:
            decision = json.loads(decision_text)
        except json.JSONDecodeError:
            try:
                decision = json.loads(repair_json(decision_text))
            except Exception:
                try:
                    decision = ast.literal_eval(decision_text)
                    if not isinstance(decision, dict):
                        raise ValueError("Parsed content is not a dictionary")
                except (ValueError, SyntaxError):
                    decision = {"clarification_question": decision_text}

        return {"success": True, "decision": decision, "raw_text": raw_text_from_ai, "error": None}

# --- Central instance ---
api_key_manager = ApiManager(api_key=GOOGLE_GEMINI_API_KEY)


def is_successful_response(response) -> bool:
    """
    Determines if the task response is considered 'success with value'.
    """
    negative_keywords = [
        "Not found", "I didn't find", "Nothing new", "No updates",
        "Everything is up to date", "no new", "nothing found"
    ]
    if isinstance(response, dict):
        if "error" in response:
            return False
        if response.get("updates") or response.get("briefing") or response.get("report") or response.get("clients"):
            return True
        if response.get("status") == "Success":
            combined_text = (response.get("message", "")).lower()
            return not any(k in combined_text for k in negative_keywords)
    elif isinstance(response, str):
        text = response.lower()
        return not any(k in text for k in negative_keywords)
    return False
