import asyncio
from app.tools.hitl_manager import pause_for_hitl, get_hitl_status, list_pending_hitl

async def test():
    # Create a fake HITL session
    session_id = await pause_for_hitl(
        reason='Test: CAPTCHA detected on GST portal',
        session_data={
            'current_url': 'https://www.gst.gov.in/login',
            'screenshot_b64': 'fake_screenshot_data',
            'cookies': [],
            'actions_remaining': [{'type': 'click', 'selector': '#submit'}],
            'matched_selector': '.g-recaptcha',
        }
    )
    print('Created session_id:', session_id)

    # Check status
    status = await get_hitl_status(session_id)
    print('Status:', status['found'], '| Age:', status['age_seconds'], 'seconds')

    # List pending
    pending = await list_pending_hitl()
    print('Pending count:', len(pending))
    print('Reason:', pending[0]['reason'])

    return session_id

session_id = asyncio.run(test())
print()
print('Use this session_id to test the API endpoints:')
print(session_id)
