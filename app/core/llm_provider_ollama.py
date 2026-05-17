import os
import json
import ast
import time
import requests
import logging
import threading
from typing import List, Optional, Any, Union

from json_repair import repair_json

from ..config import Config

# --- Settings ---
OLLAMA_BASE_URL = Config.OLLAMA_BASE_URL
OLLAMA_MODEL = Config.OLLAMA_MODEL

# Compatibility aliases for existing code
GEMINI_MODEL_NAME = OLLAMA_MODEL
GEMINI_MODEL_FOR_COMPLEX_NAME = OLLAMA_MODEL

logger = logging.getLogger(__name__)

class SimpleResponse:
    """Class mimicking the Google Generative AI response structure."""
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
                class FinishReason:
                    name = "STOP"
                self.finish_reason = FinishReason()
        return [Candidate(self.text)]

class SimpleEmbedding:
    """Class mimicking an embedding response."""
    def __init__(self, embedding: List[float]):
        self.values = embedding

class OllamaClient:
    def __init__(self, base_url: str = None, model: str = None):
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip('/')
        self.model = model or OLLAMA_MODEL

    def generate_content(self, contents: Any, **kwargs) -> str:
        """
        Supports both free text and Gemini-style message list format.
        Returns raw text response (matching Google genai SDK behavior).
        """
        model_name = self.model
        logger.info(contents)
        
        payload = {
            "model": model_name,
            "stream": False,
            "options": {
                "temperature": kwargs.get('temperature', 0.7),
                "top_p": kwargs.get('top_p', 0.9),
            }
        }

        # If we received a simple string, use /api/generate
        if isinstance(contents, str):
            payload["prompt"] = contents
            endpoint = f"{self.base_url}/api/generate"
        else:
            # If we received a message list (Gemini/OpenAI format), use /api/chat
            messages = []
            if isinstance(contents, list):
                for msg in contents:
                    role = "user" if msg.get("role") in ["user", "human"] else "assistant"
                    parts = msg.get("parts", [])
                    content_text = parts[0] if isinstance(parts[0], str) else parts[0].get("text", "")
                    messages.append({"role": role, "content": content_text})
            
            payload["messages"] = messages
            endpoint = f"{self.base_url}/api/chat"

        try:
            response = requests.post(endpoint, json=payload, timeout=180)
            response.raise_for_status()
            res_json = response.json()
            
            # Extract text based on endpoint type
            if "response" in res_json:
                output_text = res_json["response"]
            elif "message" in res_json:
                output_text = res_json["message"].get("content", "")
            else:
                output_text = ""
            
            return output_text
        except Exception as e:
            logger.error(f"Ollama API Error: {e}")
            raise

    def embed_content(self, content: Union[str, List[str]], **kwargs) -> SimpleEmbedding:
        """Generate vectors (embeddings)."""
        model_name = kwargs.get('model', self.model)
        
        # New Ollama API uses /api/embed
        payload = {
            "model": model_name,
            "input": content
        }
        
        try:
            response = requests.post(f"{self.base_url}/api/embed", json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            
            embeddings = result.get("embeddings", [])
            return SimpleEmbedding(embeddings[0] if embeddings else [])
        except Exception as e:
            logger.error(f"Ollama Embedding Error: {e}")
            return self._legacy_embeddings(content, model_name)

    def _legacy_embeddings(self, prompt: str, model: str):
        payload = {"model": model, "prompt": prompt}
        response = requests.post(f"{self.base_url}/api/embeddings", json=payload)
        return SimpleEmbedding(response.json().get("embedding", []))

    def safe_generate_content(self, prompt: str, model_name: str = None, max_retries: int = 5, backoff_factor: int = 3):
        """
        Calls generate_content with full failsafe handling:
        - 429 rate limit retries with exponential backoff
        - Response structure validation (safety filters, finish reasons, empty content)
        - JSON extraction from raw text (first '{' to last '}')
        - JSON parsing fallback chain: json.loads -> repair_json -> ast.literal_eval -> raw text fallback

        Returns dict:
            {"success": bool, "decision": parsed_dict_or_none, "raw_text": str, "error": str_or_none}
        """
        model = model_name or self.model

        # --- Step 1: Retry with exponential backoff for 429 rate limits ---
        reasoning_response = None
        for i in range(max_retries):
            try:
                reasoning_response = self.generate_content(prompt, model_name=model)
                break
            except Exception as e:
                if "429" in str(e):
                    if i == max_retries - 1:
                        return {"success": False, "decision": None, "raw_text": "", "error": f"API rate limit exceeded after {max_retries} retries"}
                    wait_time = backoff_factor * (i + 1)
                    logger.warning(f"API rate limit hit. Retrying in {wait_time} seconds... (Attempt {i+1}/{max_retries})")
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

# Central instance
api_key_manager = OllamaClient()
