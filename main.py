# main.py
import os
import sys
import threading
import asyncio

from dotenv import load_dotenv
load_dotenv()

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from flask import Flask, send_from_directory
from flask_cors import CORS
from waitress import serve

from app.utils.logger import setup_technical_logger, log_and_print
from app.core.agent import active_agent
from app.scheduler.jobs import (
    schedule_runner,
    setup_runtime_schedule,
    process_routines,
    check_and_execute_freeze_schedule,
    postpone_overdue_tasks
)   

from app.api.projects_routes import projects_bp
from app.api.tasks_routes import tasks_bp
from app.api.chat_routes import chat_bp
from app.api.system_routes import system_bp
from app.api.flows_routes import flows_bp
from app.api.missions_routes import missions_bp
from app.api.payments_routes import payments_bp
from app.api.ideas_routes import ideas_bp
from app.api.google_routes import google_bp
from app.api.calendar_routes import calendar_bp
from app.api.routines_routes import routines_bp
from app.api.yahli_routes import yahli_bp
from app.api.pages_routes import pages_bp

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
DEBUG = True
YAHLI_API_LOGGING = os.environ.get('YAHLI_API_LOGGING', 'true').lower() == 'true'

app = Flask(__name__, static_folder='client')
CORS(app)

app.register_blueprint(projects_bp, url_prefix='/api/projects')
app.register_blueprint(tasks_bp, url_prefix='/api/tasks')
app.register_blueprint(chat_bp, url_prefix='/api/chat')
app.register_blueprint(system_bp, url_prefix='/api/system')
app.register_blueprint(flows_bp, url_prefix='/api/flows')
app.register_blueprint(missions_bp, url_prefix='/api/missions')
app.register_blueprint(payments_bp, url_prefix='/api/payments')
app.register_blueprint(ideas_bp, url_prefix='/api/ideas')
app.register_blueprint(google_bp, url_prefix='/api/google')
app.register_blueprint(calendar_bp, url_prefix='/api/calendar')
app.register_blueprint(routines_bp, url_prefix='/api/routines')
app.register_blueprint(yahli_bp, url_prefix='/api/yahli')
app.register_blueprint(pages_bp, url_prefix='')

agent_instance = None
ASYNC_LOOP = None

def start_asyncio_event_loop():
    global ASYNC_LOOP
    if sys.platform.startswith('win'):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception as e:
            print(f'Warning: {e}')
    try:
        ASYNC_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(ASYNC_LOOP)
    except RuntimeError:
        ASYNC_LOOP = asyncio.get_event_loop()

    def run_loop():
        if ASYNC_LOOP:
            asyncio.set_event_loop(ASYNC_LOOP)
            ASYNC_LOOP.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    log_and_print('Asyncio event loop started in a background thread.', 'SYSTEM')

def stop_asyncio_event_loop():
    global ASYNC_LOOP
    if ASYNC_LOOP and ASYNC_LOOP.is_running():
        ASYNC_LOOP.call_soon_threadsafe(ASYNC_LOOP.stop)
        log_and_print('Asyncio event loop stopped.', 'SYSTEM')

if __name__ == '__main__':
    setup_technical_logger()
    log_and_print('Starting Yahli Gemma Agent system...', 'SYSTEM')

    try:
        agent_instance = active_agent()
        agent_instance.load_persona('show_manager')
        log_and_print('Agent loaded successfully.', 'SYSTEM')

        from app.api.projects_routes import set_active_agent as set_projects_agent
        from app.api.tasks_routes import set_active_agent as set_tasks_agent
        from app.api.chat_routes import set_active_agent as set_chat_agent
        from app.api.system_routes import set_active_agent as set_system_agent
        from app.api.flows_routes import set_active_agent as set_flows_agent
        from app.api.missions_routes import set_active_agent as set_missions_agent
        from app.api.payments_routes import set_active_agent as set_payments_agent
        from app.api.ideas_routes import set_active_agent as set_ideas_agent
        from app.api.google_routes import set_active_agent as set_google_agent
        from app.api.calendar_routes import set_active_agent as set_calendar_agent
        from app.api.routines_routes import set_active_agent as set_routines_agent
        from app.api.yahli_routes import set_active_agent as set_yahli_agent
        from app.api.pages_routes import set_active_agent as set_pages_agent
        from app.scheduler.jobs import set_scheduler_agent

        set_projects_agent(agent_instance)
        set_tasks_agent(agent_instance)
        set_chat_agent(agent_instance)
        set_system_agent(agent_instance)
        set_flows_agent(agent_instance)
        set_missions_agent(agent_instance)
        set_payments_agent(agent_instance)
        set_ideas_agent(agent_instance)
        set_google_agent(agent_instance)
        set_calendar_agent(agent_instance)
        set_routines_agent(agent_instance)
        set_yahli_agent(agent_instance)
        set_pages_agent(agent_instance)
        set_scheduler_agent(agent_instance)

    except Exception as e:
        import logging
        logging.error('CRITICAL ERROR: Could not load agent.', exc_info=True)
        agent_instance = None

    if agent_instance:
        try:
            start_asyncio_event_loop()
            setup_runtime_schedule(agent_instance)
            process_routines(agent_instance)

            import schedule
            schedule.every().day.at('00:01').do(check_and_execute_freeze_schedule, agent=agent_instance)
            schedule.every().day.at('12:05').do(process_routines, agent=agent_instance)
            schedule.every().day.at('14:00').do(process_routines, agent=agent_instance)
            schedule.every().day.at('06:00').do(postpone_overdue_tasks, agent=agent_instance)

            scheduler_thread = threading.Thread(target=schedule_runner, daemon=True)
            scheduler_thread.start()
            log_and_print('Background Scheduler thread started.', 'SYSTEM')

            log_and_print('Server is running on http://0.0.0.0:8000', 'SYSTEM')
            serve(app, host='0.0.0.0', port=8000)

        except Exception as e:
            import logging
            logging.critical(f'Server crashed: {e}', exc_info=True)
        finally:
            stop_asyncio_event_loop()
            log_and_print('Application shut down successfully.', 'SYSTEM')
    else:
        print('Failed to start because the Agent could not be initialized.')
