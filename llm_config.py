import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq

# To use Gemini instead, `pip install langchain-google-genai` and import ChatGoogleGenerativeAI.
load_dotenv()

# def get_llm():
#     """
#     Initializes the LLM with Google Gemini configuration.
#     """
#     token = os.getenv("GEMINI_API_KEY")
    
#     if not token:
#         raise ValueError("GEMINI_API_KEY not found. Check your .env file.")

#     return ChatGoogleGenerativeAI(
        
#         model="gemini-3.8-flash",
#         temperature=0.7,
#         google_api_key=token,
#         include_thoughts=True,
#     )





def get_llm():
    """
    Initializes the LLM with Groq configuration.
    """
    token = os.getenv("GROQ_API_KEY")

    if not token:
        raise ValueError("GROQ_API_KEY not found. Check your .env file.")

    return ChatGroq(
        model="openai/gpt-oss-120b",
        temperature=0.7,
        groq_api_key=token,
    )


# Usage
llm = get_llm()
