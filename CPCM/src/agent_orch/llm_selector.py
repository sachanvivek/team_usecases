from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama

def get_llm(name="openai", stream=False):
    if name == "openai":
        return ChatOpenAI(model="gpt-4", temperature=0, streaming=stream)
    elif name == "gemini":
        return ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
    elif name == "ollama":
        return ChatOllama(model="llama3.2", temperature=0)
    else:
        raise ValueError("Unsupported LLM name")
