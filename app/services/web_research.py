# app/services/web_research.py

import os
import json
import textwrap
import time
from googleapiclient.discovery import build
from typing import List, Dict, Any

# --- 1. Imports from our modules ---
from ..db.json_manager import find_records
from ..utils.logger import log_and_print
from ..core.llm_provider import api_key_manager, GEMINI_MODEL_NAME, GEMINI_MODEL_FOR_COMPLEX_NAME
from ..config import Config

# Note: TOOL_MAP imported lazily to avoid circular imports

# --- Critical settings (loaded from environment via Config) ---
GOOGLE_API_KEY = Config.GOOGLE_API_KEY
SEARCH_ENGINE_ID = Config.SEARCH_ENGINE_ID

# =========================================================================================
#  1. Search helper class (Google Search Wrapper)
# =========================================================================================

class GoogleSearch:
    def __init__(self, api_key, search_engine_id):
        if not api_key or not search_engine_id:
            raise ValueError("Google API Key and Search Engine ID must be provided.")
        self.service = build("customsearch", "v1", developerKey=api_key)
        self.search_engine_id = search_engine_id

    def search(self, queries: list, num_results: int = 5) -> list:
        """
        Executes a list of search queries using the Google Custom Search API.
        Returns a list of result sets, one for each query.

        :param queries: List of search queries (strings)
        :param num_results: Number of results to fetch per query (default 5, max 10 per API call)
        """
        all_results = []
        if not isinstance(queries, list):
            log_and_print("Search queries must be provided as a list.", "ERROR")
            return []

        for query in queries:
            try:
                # Important: Google Custom Search limit is 10 results per call
                num = min(num_results, 10)

                result = self.service.cse().list(
                    q=query,
                    cx=self.search_engine_id,
                    num=num
                ).execute()

                formatted_items = [
                    {
                        "title": item.get("title"),
                        "link": item.get("link"),
                        "snippet": item.get("snippet")
                    }
                    for item in result.get("items", [])
                ]

                all_results.append({"query": query, "results": formatted_items})

                # If more than 10 results are needed, add additional pages:
                remaining = num_results - num
                start_index = 11
                while remaining > 0:
                    paged_result = self.service.cse().list(
                        q=query,
                        cx=self.search_engine_id,
                        num=min(10, remaining),
                        start=start_index
                    ).execute()

                    for item in paged_result.get("items", []):
                        formatted_items.append({
                            "title": item.get("title"),
                            "link": item.get("link"),
                            "snippet": item.get("snippet")
                        })

                    remaining -= 10
                    start_index += 10

            except Exception as e:
                log_and_print(f"Error during Google Search for query '{query}': {e}", "ERROR")
                all_results.append({"query": query, "results": [], "error": str(e)})

        return all_results


google_search = GoogleSearch(GOOGLE_API_KEY, SEARCH_ENGINE_ID)

# =========================================================================================
#  2. Research and planning tools (The Tools)
# =========================================================================================


def generate_analytical_roadmap(agent, user_prompt: str) -> dict:
    """
    Receives a user request and generates a "Roadmap".
    Upgraded version: Includes guidance for cascade updates.
    """
    # Import TOOL_MAP lazily to avoid circular imports
    from ..core.tools_registry import TOOL_MAP
    
    # 1. Prepare a readable list of tools
    tools_description = "\n".join([
        f"- `{name}`: {info.get('description', 'N/A')}"
        for name, info in TOOL_MAP.items()
    ])

    # 2. Prepare the list of tables so the planner knows where to search
    available_tables = agent.get_available_tables()
    tables_list_str = ", ".join(available_tables)

    # 3. Build the dedicated planning prompt
    planner_prompt = textwrap.dedent(f"""
        You are an expert Task Planner for an AI agent. Your sole responsibility is to analyze the user's request and the available tools to create a precise, step-by-step action plan.

        **--- PRIMARY DIRECTIVE ---**
        **CRITICAL RULE: You are forbidden from including the `generate_analytical_roadmap` tool within the plan you create.** Do not call yourself.

        **--- CORE RULES ---**
        1.  **DO NOT EXECUTE THE TASK!** You are only the planner.
        2.  Your output MUST be a single, valid JSON object and nothing else.
        3.  The plan must be a list of steps.

        **--- SPECIAL RULE FOR RENAMING/UPDATING ENTITIES ---**
        If the user asks to rename a client, project, or entity **"everywhere"** or **"in the system"**:
        1.  You MUST create a separate step for **EVERY table** where that name might appear.
        2.  Do NOT assume updating the 'clients' table is enough. You must also update 'shows', 'communications', 'projects', etc.
        3.  Use the `update_records_by_query` tool for each table.

        **Available Tables:** {tables_list_str}

        **Available Tools:**
        {tools_description}

        **User Request:** "{user_prompt}"

        **Required Output Format Example (for renaming 'OldName' to 'NewName'):**
        {{
            "plan": [
                {{
                    "step": 1,
                    "description": "Update client name in clients table.",
                    "tool_call": {{
                        "tool_name": "update_records_by_query",
                        "parameters": {{ "table_name": "clients", "query": {{"client_name": "OldName"}}, "updates": {{"client_name": "NewName"}} }}
                    }}
                }},
                {{
                    "step": 2,
                    "description": "Update client name in shows table.",
                    "tool_call": {{
                        "tool_name": "update_records_by_query",
                        "parameters": {{ "table_name": "shows", "query": {{"client_name": "OldName"}}, "updates": {{"client_name": "NewName"}} }}
                    }}
                }},
                 {{
                    "step": 3,
                    "description": "Update client name in communications table.",
                    "tool_call": {{
                        "tool_name": "update_records_by_query",
                        "parameters": {{ "table_name": "communications", "query": {{"client_name": "OldName"}}, "updates": {{"client_name": "NewName"}} }}
                    }}
                }},
                {{
                    "step": 4,
                    "description": "Confirm system-wide update.",
                    "reasoning_prompt": "Summarize that OldName was changed to NewName in clients, shows, and communications."
                }}
            ]
        }}

        Now, build the JSON plan for the user's request.
    """)

    try:
        response = api_key_manager.generate_content(planner_prompt, model_name=GEMINI_MODEL_FOR_COMPLEX_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        roadmap = json.loads(cleaned_response)
        return roadmap
    except json.JSONDecodeError:
        return {"error": "Failed to generate a valid JSON roadmap."}
    except Exception as e:
        return {"error": str(e)}



def find_quick_answer_online(agent, query: str, num_results: int = 15, max_requests: int = 6):
    """
    Focused and improved search tool: Performs a broader internet search,
    draws more sources per query and synthesizes a precise and concise answer.
    """
    log_and_print(f"--- ⚡️ Starting quick (enhanced) internet search for question: '{query}' ---", "SYSTEM")

    # --- Step 1: AI creates diverse search queries ---
    query_generation_prompt = textwrap.dedent(f"""
    You are a professional Google search query generator.

    Your task is to transform the user's question into several (1–{max_requests}) highly effective Google search queries. 
    Each query should target a slightly different phrasing, synonym, or angle on the topic, 
    to maximize diversity and coverage.

    **Guidelines:**
    - Optimize each query for precision and relevance.
    - Add one query that explicitly targets *recent* results (e.g. add "2025", "updated", or "latest").
    - Do not include question marks, quotes, or filler words.
    - Output only valid JSON with one key: "queries".

    **Input:**
    User's Question: "{query}"

    **Output Example:**
    {{
        "queries": [
            "מנצחת יורו 2024",
            "זוכת אליפות אירופה בכדורגל 2024",
            "תוצאות גמר יורו 2024",
            "מי לקח את יורו 2024"
        ]
    }}
    """)

    try:
        response = api_key_manager.generate_content(query_generation_prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        plan = json.loads(cleaned_response)
        queries = plan.get("queries", [])
        if not queries:
            return {"error": "The AI failed to generate any search queries for the question."}
        
        log_and_print(f"   - Generated search queries: {queries}", "SYSTEM")

    except (json.JSONDecodeError, ValueError) as e:
        return {"error": f"The AI failed to generate a valid search plan. Details: {e}"}

    # --- Step 2: Perform broad searches ---
    try:
        # Use the "num_results" parameter if supported, to return more results per query
        search_results_raw = google_search.search(queries=queries, num_results=num_results)

        formatted_results_for_synthesis = ""
        result_count = 0
        for result_set in search_results_raw:
            for result in result_set.get('results', []):
                # Narrow down to only the important information
                formatted_results_for_synthesis += f"### Result\n"
                formatted_results_for_synthesis += f"**Title:** {result.get('title')}\n"
                formatted_results_for_synthesis += f"**Snippet:** {result.get('snippet')}\n\n"
                result_count += 1

        log_and_print(f"   - Collected {result_count} results in total from various sites.", "SYSTEM")

        if not formatted_results_for_synthesis:
            return {"status": "No Results", "answer": "No relevant results found to answer the question."}

    except Exception as e:
        return {"error": f"An error occurred during the Google search execution: {e}"}

    # --- Step 3: Synthesis ---
    log_and_print("   - Synthesizing answer from multiple sources...", "SYSTEM")

    synthesizer_prompt = textwrap.dedent(f"""
        You are an expert fact-checker and information synthesizer. 
        Your task is to analyze the following search results and produce a single, accurate answer in **Hebrew** to the user's question.

        **User's Original Question:** "{query}"

        **Instructions:**
        1. Provide a concise and factual answer (2–5 sentences max).
        2. Base your answer strictly on the provided results — do not assume anything not stated.
        3. If the results include multiple valid answers, list them all briefly.
        4. If there is no reliable information, say clearly: "I could not find a definitive answer online."
        5. Write naturally and fluently in Hebrew, without citations or links.

        **Search Results:**
        ---
        {formatted_results_for_synthesis}
        ---
    """)

    try:
        final_answer_response = api_key_manager.generate_content(synthesizer_prompt, model_name=GEMINI_MODEL_NAME)
        final_answer = final_answer_response
        
        log_and_print("   - ✅ Quick (enhanced) answer synthesized successfully.", "SYSTEM")
        return {"status": "Success", "answer": final_answer}

    except Exception as e:
        return {"error": f"The AI synthesizer failed to generate the final answer. Details: {e}"}




def execute_search_multi_task_prompt(agent, prompt: str, num_results_per_task: int = 5):
    """
    Performs multiple short tasks given in a single prompt.
    Breaks the request into individual tasks, executes each via search, and synthesizes a unified answer.
    """
    log_and_print(f"--- 🚀 Starting multi-task prompt execution: '{prompt}' ---", "SYSTEM")

    # --- Step 1: AI breaks the main prompt into a list of search tasks ---
    deconstructor_prompt = textwrap.dedent(f"""
    You are a master planner AI. Your job is to deconstruct a user's complex request into a series of simple, specific, and searchable questions.

    **Instructions:**
    - Analyze the user's prompt and identify every distinct piece of information they are asking for.
    - For each piece of information, formulate a clear and concise question that can be answered by a search engine.
    - Output ONLY a valid JSON object with a single key "tasks", which contains a list of these questions.

    **User's Prompt:**
    "{prompt}"

    **Output Example for "מצא לי את דרכי הקשר של גני עומר משען ובית חולים סורוקה":**
    {{
        "tasks": [
            "דרכי קשר גני עומר משען",
            "פרטי קשר בית חולים סורוקה"
        ]
    }}
    """)

    try:
        log_and_print("   - Step 1: Breaking the main request into sub-tasks...", "SYSTEM")
        response = api_key_manager.generate_content(deconstructor_prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        plan = json.loads(cleaned_response)
        tasks = plan.get("tasks", [])
        
        if not tasks:
            return {"error": "The AI failed to deconstruct the prompt into searchable tasks."}
        
        log_and_print(f"   - ✅ Identified sub-tasks: {tasks}", "SYSTEM")

    except (json.JSONDecodeError, ValueError) as e:
        return {"error": f"The AI planner failed to generate a valid JSON plan. Details: {e}"}

    # --- Step 2: Execution loop - run the tool on each sub-task ---
    task_results = {}
    log_and_print("   - Step 2: Starting iterative execution of each task...", "SYSTEM")
    
    for i, task_query in enumerate(tasks, 1):
        log_and_print(f"     - ({i}/{len(tasks)}) Performing search for: '{task_query}'", "ACTION")
        # Use your existing function as a dedicated search tool
        result = find_quick_answer_online(agent, task_query, num_results=num_results_per_task, max_requests=2)
        
        if result.get("status") == "Success":
            task_results[task_query] = result.get("answer", "No answer found.")
            log_and_print(f"     - ✅ Result found.", "SYSTEM")
        else:
            error_message = result.get("error", "An unknown error occurred during the search.")
            task_results[task_query] = f"Search error: {error_message}"
            log_and_print(f"     - ❌ Failed: {error_message}", "ERROR")

    # --- Step 3: Synthesis - AI combines all answers into a final coherent answer ---
    log_and_print("   - Step 3: Combining all results into a final answer...", "SYSTEM")

    # Prepare results for the final prompt
    formatted_results = "\n\n".join([f"**שאלה:** {q}\n**תשובה שנמצאה:** {a}" for q, a in task_results.items()])

    synthesis_prompt = textwrap.dedent(f"""
    You are a helpful assistant. Your task is to synthesize the results of multiple searches into a single, well-organized, and user-friendly answer in Hebrew.

    **Original User Request:**
    "{prompt}"

    **Collected Information (Question-Answer pairs):**
    ---
    {formatted_results}
    ---

    **Instructions:**
    1.  Address the user's original request directly.
    2.  Present the answer for each sub-task clearly.
    3.  Use markdown (like bullet points or bold text) to structure the information logically.
    4.  Do not mention the search process. Just provide the final, consolidated answer.
    5.  If some information could not be found, state that clearly for that specific part.
    6.  The entire response must be in Hebrew.
    """)

    try:
        final_response = api_key_manager.generate_content(synthesis_prompt, model_name=GEMINI_MODEL_NAME)
        combined_answer = final_response
        
        log_and_print("--- ✅ Successful completion of multi-task prompt ---", "SYSTEM")
        return {"status": "Success", "combined_answer": combined_answer}

    except Exception as e:
        return {"error": f"The final AI synthesizer failed. Details: {e}"}


def perform_advanced_research(agent, query: str):
    """
    Autonomous research agent. Breaks a general goal into multiple searches, executes them,
    and synthesizes all information into a comprehensive report with source citations.
    """
    log_and_print(f"--- 🔬 Starting advanced research on topic: '{query}' ---", "SYSTEM")

    # --- Step 1: Planning - AI builds a research plan ---
    log_and_print("   - Step 1: AI breaks the goal into focused research questions...", "SYSTEM")
    
    planner_prompt = textwrap.dedent(f"""
        You are a senior research analyst. Your task is to break down a user's high-level research goal into a series of 2-5 precise, effective Google search queries.

        **User's Research Goal:** "{query}"

        **Instructions:**
        - Think step-by-step. What information is needed to fully answer the user's goal?
        - Create queries that are distinct and cover different aspects of the topic.
        - The queries should be in the same language as the user's goal.

        **Output Format:**
        Your output MUST be a single, valid JSON object with one key: "queries". The value should be a list of the search query strings.

        **Example:**
        - User Goal: "מה היתרונות והחסרונות של מעבר ל-React Native לפיתוח מובייל?"
        - Your Output:
        {{
            "queries": [
                "React Native pros and cons 2025",
                "React Native performance vs native iOS Android",
                "שוק העבודה למפתחי React Native בישראל",
                "עלויות פיתוח אפליקציה ב-React Native"
            ]
        }}
    """)

    try:
        response = api_key_manager.generate_content(planner_prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        plan = json.loads(cleaned_response)
        queries = plan.get("queries", [])
        if not queries:
            return {"error": "The AI planner failed to generate any search queries."}
        
        log_and_print(f"   - ✅ Research plan created. The following searches will be performed: {queries}", "SYSTEM")

    except (json.JSONDecodeError, ValueError) as e:
        return {"error": f"The AI planner failed to generate a valid research plan. Details: {e}"}

    # --- Step 2: Execution - Run the planned searches ---
    log_and_print("   - Step 2: Performing all searches against Google...", "SYSTEM")
    try:
        # Here we use the API's ability to perform multiple searches in one call
        search_results_raw = google_search.search(queries=queries, num_results=10)
        
        # Prepare results for synthesis: Add an index to each result so we can cite it
        formatted_results_for_synthesis = ""
        result_index = 1
        for result_set in search_results_raw:
            for result in result_set.get('results', []):
                formatted_results_for_synthesis += f"### Result [cite:{result_index}]\n"
                formatted_results_for_synthesis += f"**Title:** {result.get('title')}\n"
                formatted_results_for_synthesis += f"**Snippet:** {result.get('snippet')}\n"
                formatted_results_for_synthesis += f"**Source:** {result.get('link')}\n\n"
                result_index += 1

        if not formatted_results_for_synthesis:
            return {"status": "No Results", "message": "The research plan was executed, but no relevant information was found online for the generated queries."}

    except Exception as e:
        return {"error": f"An error occurred during the search execution phase: {e}"}

    # --- Step 3: Synthesis - AI writes a report based on the collected information ---
    log_and_print("   - Step 3: AI combines all collected information into a summary report...", "SYSTEM")

    synthesizer_prompt = textwrap.dedent(f"""
        You are an expert information synthesizer. You have been provided with a collection of raw search results from multiple queries to answer a user's goal.

        **The User's Original Research Goal:** "{query}"

        **Your Task:**
        Your mission is to synthesize ALL the relevant information from the provided search results into a single, comprehensive, well-structured, and easy-to-read report in Hebrew.

        **Critical Instructions:**
        1.  **Synthesize, Don't List:** Do not just summarize each result one by one. Weave the information together into a coherent narrative that directly addresses the user's goal.
        2.  **Structure is Key:** Use Markdown for formatting. Use headings (`##`), bold text (`**`), and bullet points (`*`) to make the report clear and scannable.
        3.  **Cite Everything:** This is the most important rule. For EVERY piece of information or claim you include in your report, you MUST end the sentence with a citation marker in the format `[cite:INDEX]`, referencing the result number from the provided data. You can cite multiple sources for a single sentence, like `[cite:1, 4]`.
        4.  **Be Objective:** Stick to the information found in the search results. Do not add your own opinions or information not present in the sources.
        5.  **Language:** The final report must be in Hebrew.

        **Here are the raw search results:**
        ---
        {formatted_results_for_synthesis}
        ---

        Now, generate the final, comprehensive report.
    """)

    try:
        final_report_response = api_key_manager.generate_content(synthesizer_prompt, model_name=GEMINI_MODEL_NAME)
        final_report = final_report_response
        
        log_and_print("   - ✅ Final research report prepared successfully.", "SYSTEM")
        return {"status": "Success", "report": final_report}

    except Exception as e:
        return {"error": f"The AI synthesizer failed to generate the final report. Details: {e}"}
