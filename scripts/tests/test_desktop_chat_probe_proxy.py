"""No real credentials or network beyond two loopback listeners."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from fastapi import HTTPException

from tcode_agent.providers.base import ChatMessage
from tcode_agent.server.desktop_models import probe, provider


@asynccontextmanager
async def listener(handler):
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        yield f'http://127.0.0.1:{runner.addresses[0][1]}'
    finally:
        await runner.cleanup()


@pytest.mark.parametrize('protocol', ['openai_compatible', 'anthropic', 'ollama'])
@pytest.mark.parametrize('route', ['proxy', 'proxy_rejected', 'no_proxy'])
def test_probe_and_real_provider_use_same_route(monkeypatch, protocol, route):
    from tcode_agent.providers.anthropic import AnthropicProvider
    from tcode_agent.providers.openai import OpenAIProvider

    async def run():
        visits = []

        def handler(name):
            async def respond(request):
                visits.append((name, request.path))
                if route == 'proxy_rejected':
                    return web.Response(status=403)
                if protocol == 'anthropic':
                    return web.json_response(dict(type='message', id='m', model='fixture', role='assistant',
                        content=[dict(type='text', text='OK')], stop_reason='end_turn', usage=dict(input_tokens=1, output_tokens=1)))
                return web.json_response(dict(id='m', model='fixture', object='chat.completion', created=1,
                    choices=[dict(index=0, message=dict(role='assistant', content='OK'), finish_reason='stop')]))
            return respond

        async with listener(handler('origin')) as origin, listener(handler('proxy')) as proxy:
            for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY'):
                for key in (name, name.lower()):
                    monkeypatch.delenv(key, raising=False)
            monkeypatch.setenv('HTTP_PROXY', proxy)
            monkeypatch.setenv('NO_PROXY', 'localhost,127.0.0.1,::1' if route == 'no_proxy' else 'localhost')
            service = dict(id='local', kind='chat', protocol=protocol, base_url=origin, models=['fixture'])
            cls = AnthropicProvider if protocol == 'anthropic' else OpenAIProvider
            chat = cls(api_key='fixture', base_url=provider(service)['options']['baseURL'], model='fixture', max_retries=0)
            try:
                if route == 'proxy_rejected':
                    with pytest.raises(HTTPException, match='403.*模型服务或代理'):
                        await probe(service, 'fixture', 'fixture')
                    with pytest.raises(Exception):
                        await chat.chat([ChatMessage(role='user',content='Reply OK')])
                else:
                    assert (await probe(service, 'fixture', 'fixture'))['ok']
                    assert (await chat.chat([ChatMessage(role='user',content='Reply OK')])).content == 'OK'
            finally:
                await chat.close()
            target = 'origin' if route == 'no_proxy' else 'proxy'
            assert [n for n, _ in visits] == [target, target]
            assert visits[0][1] == visits[1][1]

    asyncio.run(run())


@pytest.mark.parametrize(('status', 'hint'), [(403, '403.*模型服务或代理'), (407, '代理认证失败'), (502, '无法连接模型服务')])
def test_https_proxy_tunnel_failure_never_falls_back_to_direct(monkeypatch, status, hint):
    from tcode_agent.providers.openai import OpenAIProvider
    async def run():
        requests = []
        async def reject(reader, writer):
            request = await reader.readuntil(b'\r\n\r\n')
            requests.append(request.splitlines()[0])
            writer.write(f'HTTP/1.1 {status} private-reason\r\nContent-Length: 0\r\n\r\n'.encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        async with await asyncio.start_server(reject, '127.0.0.1', 0) as proxy:
            monkeypatch.setenv('https_proxy', f'http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}')
            for key in ('NO_PROXY','no_proxy'):
                monkeypatch.delenv(key, raising=False)
            base = 'https://proxy-test.invalid/v1'
            service = dict(kind='chat', protocol='openai_compatible', base_url=base, models=['fixture'])
            with pytest.raises(HTTPException, match=hint) as error:
                await probe(service,'fixture','fixture')
            assert 'private-reason' not in error.value.detail
            chat = OpenAIProvider(api_key='fixture',base_url=base,model='fixture',max_retries=0)
            try:
                with pytest.raises(Exception, match='connection error'):
                    await chat.chat([ChatMessage(role='user',content='Reply OK')])
            finally:
                await chat.close()
            assert requests == [b'CONNECT proxy-test.invalid:443 HTTP/1.1'] * 2
    asyncio.run(run())


@pytest.mark.parametrize('url', ['bad-scheme://private-user@localhost', 'http://localhost:private-port'])
def test_invalid_proxy_is_not_a_model_response(monkeypatch, url):
    monkeypatch.setenv('https_proxy', url)
    monkeypatch.setenv('no_proxy', '')
    with pytest.raises(HTTPException, match='代理地址') as error:
        asyncio.run(probe(dict(kind='chat', protocol='openai_compatible', base_url='https://model.invalid',
                               models=['fixture']), '', 'fixture'))
    assert 'private-' not in error.value.detail


@pytest.mark.parametrize(('kind', 'status', 'hint'), [
    ('chat', 407, '代理认证失败'), ('chat', 200, '服务返回的格式与所选协议不兼容'),
    ('embedding', 407, '服务返回 HTTP 407，请检查接口配置'),
    ('embedding', 403, '服务拒绝访问，请检查账号权限'),
    ('embedding', 200, '服务返回的格式与所选协议不兼容'),
])
def test_desktop_response_hints(monkeypatch, kind, status, hint):
    async def respond(_):
        return web.Response(status=status, text='not JSON')
    async def run():
        async with listener(respond) as url:
            service = dict(kind=kind, protocol='ollama', base_url=url, models=['fixture'])
            with pytest.raises(HTTPException, match=hint):
                await probe(service, '', 'fixture')
        if kind == 'embedding':
            with pytest.raises(HTTPException) as error:
                await probe(service, '', 'fixture')
            assert error.value.detail == '无法连接模型服务，请检查地址、网络和证书'
    monkeypatch.setenv('no_proxy', '127.0.0.1')
    asyncio.run(run())
