import asyncio
from app.tools.playwright_tool import run_browser

async def test():
    result = await run_browser(
        url='https://services.gst.gov.in/services/login',
        actions=[
            {'type': 'get_text', 'selector': 'h1'},
            {'type': 'screenshot'},
        ]
    )
    print('Success:', result.success)
    print('URL:', result.current_url)
    print('HITL needed:', result.hitl_needed)
    print('Scraped h1:', result.data.get('h1', 'not found'))
    print('Screenshot captured:', bool(result.screenshot_b64))

asyncio.run(test())