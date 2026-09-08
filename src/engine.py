"""Module 4 Engine: Hybrid Memory Engine Orchestrator.

Combines Working Memory, Short-Term Memory Buffer, Archival Vector Store,
and the Litellm SDK into a real-time memory-augmented LLM pipeline with Provider Fallback Mesh.
"""
import os
import time
import logging
import urllib.request
import json
from typing import Optional, Tuple, Dict, List, Any
# pyrefly: ignore [missing-import]
import openai
from google import genai
# pyrefly: ignore [missing-import]
from google.genai import errors as genai_errors
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

from src.models import WorkingMemory, Turn, ContextPayload
from src.state_extractor import StateExtractor
from src.archival_memory import ArchivalMemoryManager
from src.short_term_memory import ShortTermMemoryBuffer
from src.prompt_assembler import DynamicPromptAssembler

load_dotenv()

class CloudMeshRouter:
    """Manages multi-key and multi-provider fallback routing."""
    
    def __init__(self):
        # Tiered list of providers (Quality & Speed prioritized)
        self.tiers = [
            {"provider": "Groq", "env_var": "GROQ_API_KEYS", "base_url": "https://api.groq.com/openai/v1", "fallback_model": "llama-3.3-70b-versatile", "context_limit": 128000},
            {"provider": "Cerebras", "env_var": "CEREBRAS_API_KEYS", "base_url": "https://api.cerebras.ai/v1", "fallback_model": "llama-3.3-70b", "context_limit": 128000},
            {"provider": "Gemini", "env_var": "GEMINI_API_KEYS", "base_url": None, "fallback_model": "gemini-2.5-flash", "context_limit": 1000000},
            {"provider": "Mistral", "env_var": "MISTRAL_API_KEYS", "base_url": "https://api.mistral.ai/v1", "fallback_model": "codestral-latest", "context_limit": 32000},
            {"provider": "OpenRouter", "env_var": "OPENROUTER_API_KEYS", "base_url": "https://openrouter.ai/api/v1", "fallback_model": "openrouter/auto", "context_limit": 64000},
        ]
        
        self.key_status = {} # Map of (provider, key) -> {"status": "Online", "cooldown_until": 0.0}
        self.provider_keys = {} # Map of provider -> list of keys
        self.discovered_models = {} # Map of provider -> active model
        self.reload_keys_from_env()

    def reload_keys_from_env(self, custom_keys: Optional[Dict[str, str]] = None):
        """Load keys from environment or custom dict (from UI) and auto-discover models."""
        if custom_keys is None:
            custom_keys = {}
        
        for tier in self.tiers:
            provider = tier["provider"]
            
            # Prefer custom_keys over environment variables (plural then singular)
            keys_str = custom_keys.get(tier["env_var"]) or os.environ.get(tier["env_var"]) or os.environ.get(tier["env_var"][:-1])
            
            if keys_str:
                keys = [k.strip() for k in keys_str.split(",") if k.strip()]
            else:
                keys = []
                
            self.provider_keys[provider] = keys
            
            for key in keys:
                if (provider, key) not in self.key_status:
                    self.key_status[(provider, key)] = {"status": "Online", "cooldown_until": 0.0}
            
            # Perform Model Auto-Discovery
            if keys:
                import threading
                t = threading.Thread(target=self._discover_model, args=(tier, keys[0]))
                t.daemon = True
                t.start()
            else:
                self.discovered_models[provider] = tier["fallback_model"]

    def _discover_model(self, tier: dict, key: str):
        provider = tier["provider"]
        fallback = tier["fallback_model"]
        base_url = tier.get("base_url")
        
        try:
            if provider == "OpenRouter":
                req = urllib.request.Request("https://openrouter.ai/api/v1/models")
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode())
                    free_models = []
                    for m in data.get("data", []):
                        pricing = m.get("pricing", {})
                        if pricing.get("prompt") == "0" and pricing.get("completion") == "0":
                            free_models.append(m)
                    
                    if free_models:
                        best_free = free_models[0]["id"]
                        for preferred in ["llama-3.3-70b", "llama-3.1-70b", "qwen-2.5-72b", "deepseek"]:
                            for m in free_models:
                                if preferred in m["id"].lower():
                                    best_free = m["id"]
                                    break
                            if best_free != free_models[0]["id"]:
                                break
                        self.discovered_models[provider] = best_free
                        logging.info(f"[OpenRouter] Auto-discovered free model: {best_free}")
                        return
                        
            elif provider == "Gemini":
                client = genai.Client(api_key=key)
                models = client.models.list()
                flash_models = []
                for m in models:
                    name = getattr(m, "name", "")
                    actions = getattr(m, "supported_actions", [])
                    if name and "generateContent" in actions and "flash" in name.lower() and "vision" not in name.lower():
                        flash_models.append(name.replace("models/", ""))
                
                if flash_models:
                    # Sort alphabetically, which conveniently places higher versions (3.6) above lower (1.5)
                    flash_models.sort(reverse=True)
                    best_gemini = flash_models[0]
                    self.discovered_models[provider] = best_gemini
                    logging.info(f"[Gemini] Auto-discovered model: {best_gemini}")
                    return

            else:
                client = openai.OpenAI(api_key=key, base_url=base_url)
                models = client.models.list()
                model_ids = [m.id for m in models.data]
                
                if model_ids:
                    best_model = model_ids[0]
                    for preferred in ["llama-3.3-70b", "llama3.3", "codestral", "mixtral", "llama-3.1-70b"]:
                        for mid in model_ids:
                            if preferred in mid.lower():
                                best_model = mid
                                break
                        if best_model != model_ids[0]:
                            break
                    self.discovered_models[provider] = best_model
                    logging.info(f"[{provider}] Auto-discovered model: {best_model}")
                    return

        except Exception as e:
            logging.warning(f"[{provider}] Auto-discovery failed: {e}. Using fallback {fallback}")
            
        self.discovered_models[provider] = fallback

    def _get_next_available_key(self, required_tokens: int = 0) -> Tuple[Optional[dict], Optional[str]]:
        """Returns (tier_dict, key) for the next available key in the mesh."""
        current_time = time.time()
        
        valid_tiers = []
        for tier in self.tiers:
            context_limit = tier.get("context_limit", 8000)
            if required_tokens > context_limit * 0.85:
                logging.info(f"Skipping {tier['provider']} because required tokens ({required_tokens}) exceeds safe limit ({int(context_limit * 0.85)})")
                continue
            valid_tiers.append(tier)
            
        # Fallback to largest available if NONE fit
        if not valid_tiers:
            valid_tiers = [max(self.tiers, key=lambda x: x.get("context_limit", 0))]
            logging.warning(f"Required tokens ({required_tokens}) exceeds all models. Forcing fallback to {valid_tiers[0]['provider']} and truncating.")

        for tier in valid_tiers:
            provider = tier["provider"]
            
            keys = self.provider_keys.get(provider, [])
            for key in keys:
                status = self.key_status.get((provider, key))
                if not status:
                    continue
                
                if status["status"] == "Cooling Down":
                    if current_time >= status["cooldown_until"]:
                        status["status"] = "Online"
                        logging.info(f"[{provider}] Key reactivated after cooldown.")
                    else:
                        continue
                
                if status["status"] == "Online":
                    return tier, key
                    
        return None, None

    def _handle_api_error(self, e: Exception, provider: str, key: str, key_suffix: str):
        err_msg = str(e).lower()
        is_rate_limit = False
        is_auth_error = False
        
        if provider == "Gemini" and isinstance(e, genai_errors.APIError):
            if e.code == 429:
                is_rate_limit = True
            elif e.code in [401, 403]:
                is_auth_error = True
        elif isinstance(e, openai.RateLimitError):
            is_rate_limit = True
        elif isinstance(e, openai.AuthenticationError):
            is_auth_error = True
        elif "429" in err_msg or "rate limit" in err_msg or "resource_exhausted" in err_msg:
            is_rate_limit = True
        elif "401" in err_msg or "403" in err_msg or "unauthorized" in err_msg or "402" in err_msg or "insufficient_quota" in err_msg:
            is_auth_error = True
        
        if is_rate_limit:
            logging.warning(f"[{provider}] Rate Limit Exceeded for key ...{key_suffix}. Cooling down for 60s.")
            self.key_status[(provider, key)]["status"] = "Cooling Down"
            self.key_status[(provider, key)]["cooldown_until"] = time.time() + 60.0
        elif is_auth_error:
            logging.warning(f"[{provider}] Authentication/Quota Error for key ...{key_suffix}: {e}. Marking as Exhausted.")
            self.key_status[(provider, key)]["status"] = "Exhausted"
        else:
            logging.warning(f"[{provider}] API Error for key ...{key_suffix}: {e}. Cooling down for 60s.")
            self.key_status[(provider, key)]["status"] = "Cooling Down"
            self.key_status[(provider, key)]["cooldown_until"] = time.time() + 60.0

    def generate_content(self, payload_kwargs: dict, required_tokens: int = 0) -> Tuple[str, ContextPayload, dict]:
        """Iterates over the mesh, generating content and falling back on errors."""
        while True:
            tier, key = self._get_next_available_key(required_tokens)
            
            if not tier:
                logging.error("All providers and keys exhausted!")
                empty_payload = DynamicPromptAssembler.assemble(**payload_kwargs, max_prompt_tokens=8000)
                return (
                    "⚠️ **All Providers Exhausted**\n\nNo available API keys in the mesh. "
                    "Please check your API keys or wait for cooldowns to expire.",
                    empty_payload,
                    {"provider": "None", "model": "None", "key_suffix": "None"}
                )
                
            provider = tier["provider"]
            model = self.discovered_models.get(provider, tier["fallback_model"])
            key_suffix = key[-4:] if len(key) > 4 else key
            
            try:
                logging.info(f"Attempting generation with {provider} ({model}) using key ending in ...{key_suffix}")
                
                # Dynamically assemble the exact payload for this specific model's context limit
                context_limit = tier.get("context_limit", 8000)
                payload = DynamicPromptAssembler.assemble(**payload_kwargs, max_prompt_tokens=int(context_limit * 0.9))
                prompt = payload.full_prompt
                
                if provider == "Gemini":
                    client = genai.Client(api_key=key)
                    response = client.models.generate_content(
                        model=model,
                        contents=prompt
                    )
                    response_text = response.text or ""
                else:
                    client = openai.OpenAI(api_key=key, base_url=tier.get("base_url"))
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    response_text = response.choices[0].message.content or ""
                
                metadata = {"provider": provider, "model": model, "key_suffix": key_suffix, "context_limit": context_limit}
                return response_text, payload, metadata
                
            except Exception as e:
                self._handle_api_error(e, provider, key, key_suffix)

    def generate_content_stream(self, payload_kwargs: dict, required_tokens: int = 0) -> Tuple[Any, ContextPayload, dict]:
        """Iterates over the mesh, generating content as a stream and falling back on errors."""
        while True:
            tier, key = self._get_next_available_key(required_tokens)
            
            if not tier:
                logging.error("All providers and keys exhausted!")
                empty_payload = DynamicPromptAssembler.assemble(**payload_kwargs, max_prompt_tokens=8000)
                def empty_stream():
                    yield "⚠️ **All Providers Exhausted**\n\nNo available API keys in the mesh. Please check your API keys or wait for cooldowns to expire."
                return empty_stream(), empty_payload, {"provider": "None", "model": "None", "key_suffix": "None"}
                
            provider = tier["provider"]
            model = self.discovered_models.get(provider, tier["fallback_model"])
            key_suffix = key[-4:] if len(key) > 4 else key
            
            try:
                logging.info(f"Attempting streaming generation with {provider} ({model}) using key ending in ...{key_suffix}")
                
                context_limit = tier.get("context_limit", 8000)
                payload = DynamicPromptAssembler.assemble(**payload_kwargs, max_prompt_tokens=int(context_limit * 0.9))
                prompt = payload.full_prompt
                
                if provider == "Gemini":
                    client = genai.Client(api_key=key)
                    response_stream = client.models.generate_content_stream(
                        model=model,
                        contents=prompt
                    )
                    iterator = iter(response_stream)
                    first_chunk = next(iterator)
                    
                    def gen(c, i, fc):
                        if fc.text: yield fc.text
                        for chunk in i:
                            if chunk.text: yield chunk.text
                    
                    metadata = {"provider": provider, "model": model, "key_suffix": key_suffix, "context_limit": context_limit}
                    return gen(client, iterator, first_chunk), payload, metadata
                else:
                    client = openai.OpenAI(api_key=key, base_url=tier.get("base_url"))
                    response_stream = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        stream=True
                    )
                    iterator = iter(response_stream)
                    
                    def generate():
                        for chunk in response_stream:
                            if chunk.choices and chunk.choices[0].delta.content:
                                yield chunk.choices[0].delta.content
                    
                    metadata = {"provider": provider, "model": model, "key_suffix": key_suffix, "context_limit": context_limit}
                    return generate(), payload, metadata
                
            except Exception as e:
                self._handle_api_error(e, provider, key, key_suffix)


class HybridMemoryEngine:
    """Core Python Engine sitting between User and LLM API."""

    def __init__(
        self,
        working_memory: Optional[WorkingMemory] = None,
        short_term_window: int = 4,
        db_dir: str = "./chroma_db",
        archival_manager: Optional[ArchivalMemoryManager] = None,
        mesh_router: Optional[CloudMeshRouter] = None,
    ):
        self.working_memory = working_memory or WorkingMemory(
            persona="Expert Assistant and Software Architect",
            rules=["Maintain high technical accuracy.", "Be clear and concise."],
            project_scope="LLM Hybrid Memory System",
            constraints=["Never violate system persona."]
        )

        # Initialize Archival Store & Short-Term Buffer
        self.archival_memory = archival_manager or ArchivalMemoryManager(db_dir=db_dir)
        self.short_term_memory = ShortTermMemoryBuffer(
            max_turns=short_term_window,
            archival_manager=self.archival_memory
        )

        # Initialize Cloud Mesh Router
        self.mesh_router = mesh_router or CloudMeshRouter()

    def ingest_transcript(self, raw_transcript: str, session_id: str = "default") -> int:
        """
        Ingests a heavy chat transcript.
        Extracts Working Memory and loads older turns into Archival Memory.
        """
        wm, turns = StateExtractor.parse_transcript_text(raw_transcript)
        self.working_memory = wm

        # If transcript contains more turns than short-term window size N,
        # fill short-term with last N turns and archive all preceding turns.
        n = self.short_term_memory.max_turns
        if len(turns) > n:
            archive_turns = turns[:-n]
            recent_turns = turns[-n:]

            self.archival_memory.add_turns(archive_turns, session_id=session_id)
            for t in recent_turns:
                self.short_term_memory.add_turn(t, session_id=session_id)
        else:
            for t in turns:
                self.short_term_memory.add_turn(t, session_id=session_id)

        return len(turns)

    def process_turn(self, user_input: str, session_id: str = "default") -> Tuple[str, ContextPayload, dict]:
        """
        Processes a single user turn synchronously.
        """
        # Dynamic Intent Detection for Rules
        if user_input.lower().startswith("new rule:"):
            new_rule = user_input.split(":", 1)[1].strip()
            if new_rule:
                self.working_memory.rules.append(new_rule)
                
        global_keywords = ["list all", "overview", "summary", "topics", "discussed"]
        intent = "SPECIFIC_INTENT"
        for kw in global_keywords:
            if kw in user_input.lower():
                intent = "GLOBAL_INTENT"
                break
                
        retrieved_snippets = []
        if intent == "SPECIFIC_INTENT":
            retrieved_snippets = self.archival_memory.search_relevant(
                query=user_input, 
                session_id=session_id, 
                top_k=2
            )

        history = self.short_term_memory.get_history()

        payload_kwargs = {
            "working_memory": self.working_memory,
            "user_input": user_input,
            "short_term_turns": history,
            "archival_snippets": retrieved_snippets
        }
        
        required_tokens = DynamicPromptAssembler.calculate_required_tokens(**payload_kwargs)

        response_text, payload, metadata = self.mesh_router.generate_content(payload_kwargs, required_tokens)
        metadata["token_count"] = DynamicPromptAssembler._count_tokens(payload.full_prompt)

        new_turn = Turn(user_message=user_input, assistant_message=response_text)
        self.short_term_memory.add_turn(new_turn, session_id=session_id)

        return response_text, payload, metadata

    def process_turn_stream(self, user_input: str, session_id: str = "default") -> Tuple[Any, ContextPayload, dict]:
        """
        Processes a single user turn asynchronously and yields streaming chunks.
        """
        # Dynamic Intent Detection for Rules
        if user_input.lower().startswith("new rule:"):
            new_rule = user_input.split(":", 1)[1].strip()
            if new_rule:
                self.working_memory.rules.append(new_rule)
                
        global_keywords = ["list all", "overview", "summary", "topics", "discussed"]
        intent = "SPECIFIC_INTENT"
        for kw in global_keywords:
            if kw in user_input.lower():
                intent = "GLOBAL_INTENT"
                break
                
        retrieved_snippets = []
        if intent == "SPECIFIC_INTENT":
            retrieved_snippets = self.archival_memory.search_relevant(
                query=user_input, 
                session_id=session_id, 
                top_k=2
            )

        history = self.short_term_memory.get_history()

        payload_kwargs = {
            "working_memory": self.working_memory,
            "user_input": user_input,
            "short_term_turns": history,
            "archival_snippets": retrieved_snippets
        }
        
        required_tokens = DynamicPromptAssembler.calculate_required_tokens(**payload_kwargs)

        chunk_gen, payload, metadata = self.mesh_router.generate_content_stream(payload_kwargs, required_tokens)
        metadata["token_count"] = DynamicPromptAssembler._count_tokens(payload.full_prompt)

        def wrapper():
            full_response = ""
            for chunk in chunk_gen:
                full_response += chunk
                yield chunk
            
            new_turn = Turn(user_message=user_input, assistant_message=full_response)
            self.short_term_memory.add_turn(new_turn, session_id=session_id)

        return wrapper(), payload, metadata
