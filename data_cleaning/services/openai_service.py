"""Application adapter for the reusable model client."""
import time
from chefmate_core.ai import AIClient
from data_cleaning.config import ROOT, service_settings
from data_cleaning.services.logging_service import log_event


class OpenAIService(AIClient):
    def __init__(self, repo, client=None, sleeper=time.sleep):
        self.repo = repo
        super().__init__(service_settings(), client, sleeper, on_call=repo.service_call,
                         on_error=log_event, prompt_dir=ROOT / "prompts")
