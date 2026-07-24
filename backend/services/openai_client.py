from os import getenv
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()


class MissingGroqApiKeyClient:
    class Chat:
        class Completions:
            async def create(self, *args, **kwargs):
                raise RuntimeError("GROQ_API_KEY is not configured.")

        completions = Completions()

    chat = Chat()


groq_api_key = getenv("GROQ_API_KEY")
openai_client = AsyncGroq(api_key=groq_api_key) if groq_api_key else MissingGroqApiKeyClient()
