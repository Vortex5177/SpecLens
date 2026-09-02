import asyncio
import collections
import random
from datetime import datetime

import pandas as pd
from django.conf import settings
from django.db import models
from django.conf.urls import url
from django.http import HttpResponse
from langchain import LLMChain, PromptTemplate
from langchain.chat_models import ChatOpenAI
from langchain.agents import initialize_agent, Tool


def parse_bool(value):
    from distutils.util import strtobool
    return bool(strtobool(value))


def get_random_item(items):
    return random.sample(items, 1)[0]


def check_type(obj):
    if isinstance(obj, collections.Mapping):
        return "dict-like"
    if isinstance(obj, collections.Iterable):
        return "iterable"
    return "other"


async def run_tasks():
    loop = asyncio.get_event_loop()
    tasks = [loop.create_task(asyncio.sleep(1)) for _ in range(3)]
    await asyncio.gather(*tasks)


def get_current_time():
    return datetime.utcnow()


def format_list(items):
    from typing import List, Dict, Optional
    result: List[Dict[str, Optional[str]]] = []
    for item in items:
        result.append({"name": item, "value": None})
    return result


def append_rows(df, new_rows):
    for row in new_rows:
        df = df.append(row, ignore_index=True)
    return df


def iterate_columns(df):
    for col_name, col_data in df.iteritems():
        print(f"Column: {col_name}, dtype: {col_data.dtype}")


def check_missing(df):
    missing = df.isna().sum()
    total = len(df)
    return missing / total


def convert_dtype(df, col):
    df[col] = df[col].astype("float64")
    return df


def filter_dataframe(df, condition):
    return df.query(condition)


def create_llm_chain():
    template = "What is the capital of {country}?"
    prompt = PromptTemplate.from_template(template)
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
    chain = LLMChain(llm=llm, prompt=prompt)
    return chain


def create_agent(tools):
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
    agent = initialize_agent(
        tools=tools,
        llm=llm,
        agent="zero-shot-react-description",
        verbose=True,
    )
    return agent


def load_tools():
    from langchain.agents import load_tools
    return load_tools(["serpapi", "llm-math"])


def create_chat_model():
    from langchain.chat_models import ChatOpenAI
    return ChatOpenAI(model="gpt-3.5-turbo")


class Article(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    author = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


def get_articles(request):
    articles = Article.objects.all()
    return HttpResponse(f"Articles: {articles.count()}")


urlpatterns = [
    url(r"^articles/$", get_articles),
    url(r"^articles/(?P<id>\d+)/$", get_articles),
]


def get_timezone():
    from django.utils.timezone import utc
    return utc


def check_settings():
    return settings.USE_TZ


def analyze_data(df, query):
    summary = df.describe()
    missing = df.isna().sum()

    template = """
    Analyze this data summary:
    {summary}

    Missing values:
    {missing}

    User query: {query}

    Provide insights.
    """
    prompt = PromptTemplate.from_template(template)
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
    chain = LLMChain(llm=llm, prompt=prompt)

    result = chain.run(
        summary=summary.to_string(),
        missing=missing.to_string(),
        query=query,
    )
    return result


def web_scraper_agent():
    from langchain.agents import initialize_agent, Tool

    def search_web(query):
        return f"Search results for: {query}"

    tools = [
        Tool(
            name="Web Search",
            func=search_web,
            description="Search the web for information",
        ),
    ]

    llm = ChatOpenAI(model="gpt-3.5-turbo")
    agent = initialize_agent(
        tools=tools,
        llm=llm,
        agent="zero-shot-react-description",
    )
    return agent
