import functools
import os
import shutil
import tempfile
from unittest import mock
from unittest.mock import MagicMock

import pytest

from aiohttp import abc, web
from aiohttp.web_urldispatcher import SystemRoute


@pytest.fixture(scope='function')
def tmp_dir_path(request):
    """
    Give a path for a temporary directory
    The directory is destroyed at the end of the test.
    """
    # Temporary directory.
    tmp_dir = tempfile.mkdtemp()

    def teardown():
        # Delete the whole directory:
        shutil.rmtree(tmp_dir)

    request.addfinalizer(teardown)
    return tmp_dir


@pytest.mark.parametrize("show_index,status,prefix,data",
                         [(False, 403, '/', None),
                          (True, 200, '/',
                           b'<html>\n<head>\n<title>Index of /.</title>\n'
                           b'</head>\n<body>\n<h1>Index of /.</h1>\n<ul>\n'
                           b'<li><a href="/my_dir">my_dir/</a></li>\n'
                           b'<li><a href="/my_file">my_file</a></li>\n'
                           b'</ul>\n</body>\n</html>'),
                          (True, 200, '/static',
                           b'<html>\n<head>\n<title>Index of /.</title>\n'
                           b'</head>\n<body>\n<h1>Index of /.</h1>\n<ul>\n'
                           b'<li><a href="/static/my_dir">my_dir/</a></li>\n'
                           b'<li><a href="/static/my_file">my_file</a></li>\n'
                           b'</ul>\n</body>\n</html>')])
async def test_access_root_of_static_handler(tmp_dir_path, loop, test_client,
                                             show_index, status, prefix, data):
    """
    Tests the operation of static file server.
    Try to access the root of static file server, and make
    sure that correct HTTP statuses are returned depending if we directory
    index should be shown or not.
    """
    # Put a file inside tmp_dir_path:
    my_file_path = os.path.join(tmp_dir_path, 'my_file')
    with open(my_file_path, 'w') as fw:
        fw.write('hello')

    my_dir_path = os.path.join(tmp_dir_path, 'my_dir')
    os.mkdir(my_dir_path)

    my_file_path = os.path.join(my_dir_path, 'my_file_in_dir')
    with open(my_file_path, 'w') as fw:
        fw.write('world')

    app = web.Application()

    # Register global static route:
    app.router.add_static(prefix, tmp_dir_path, show_index=show_index)
    client = await test_client(app)

    # Request the root of the static directory.
    r = await client.get(prefix)
    assert r.status == status

    if data:
        assert r.headers['Content-Type'] == "text/html; charset=utf-8"
        read_ = (await r.read())
        assert read_ == data


@pytest.mark.parametrize('data', ['hello world'])
async def test_follow_symlink(tmp_dir_path, loop, test_client, data):
    """
    Tests the access to a symlink, in static folder
    """
    my_dir_path = os.path.join(tmp_dir_path, 'my_dir')
    os.mkdir(my_dir_path)

    my_file_path = os.path.join(my_dir_path, 'my_file_in_dir')
    with open(my_file_path, 'w') as fw:
        fw.write(data)

    my_symlink_path = os.path.join(tmp_dir_path, 'my_symlink')
    os.symlink(my_dir_path, my_symlink_path)

    app = web.Application()

    # Register global static route:
    app.router.add_static('/', tmp_dir_path, follow_symlinks=True)
    client = await test_client(app)

    # Request the root of the static directory.
    r = await client.get('/my_symlink/my_file_in_dir')
    assert r.status == 200
    assert (await r.text()) == data


@pytest.mark.parametrize('dir_name,filename,data', [
    ('', 'test file.txt', 'test text'),
    ('test dir name', 'test dir file .txt', 'test text file folder')
])
async def test_access_to_the_file_with_spaces(tmp_dir_path, loop, test_client,
                                              dir_name, filename, data):
    """
    Checks operation of static files with spaces
    """

    my_dir_path = os.path.join(tmp_dir_path, dir_name)

    if dir_name:
        os.mkdir(my_dir_path)

    my_file_path = os.path.join(my_dir_path, filename)

    with open(my_file_path, 'w') as fw:
        fw.write(data)

    app = web.Application()

    url = os.path.join('/', dir_name, filename)

    app.router.add_static('/', tmp_dir_path)
    client = await test_client(app)

    r = await client.get(url)
    assert r.status == 200
    assert (await r.text()) == data


async def test_access_non_existing_resource(tmp_dir_path, loop, test_client):
    """
    Tests accessing non-existing resource
    Try to access a non-exiting resource and make sure that 404 HTTP status
    returned.
    """
    app = web.Application()

    # Register global static route:
    app.router.add_static('/', tmp_dir_path, show_index=True)
    client = await test_client(app)

    # Request the root of the static directory.
    r = await client.get('/non_existing_resource')
    assert r.status == 404


@pytest.mark.parametrize('registered_path,request_url', [
    ('/a:b', '/a:b'),
    ('/a@b', '/a@b'),
    ('/a:b', '/a%3Ab'),
])
async def test_url_escaping(loop, test_client, registered_path, request_url):
    """
    Tests accessing a resource with
    """
    app = web.Application()

    def handler(_):
        return web.Response()
    app.router.add_get(registered_path, handler)
    client = await test_client(app)

    r = await client.get(request_url)
    assert r.status == 200


async def test_handler_metadata_persistence():
    """
    Tests accessing metadata of a handler after registering it on the app
    router.
    """
    app = web.Application()

    async def async_handler(_):
        """Doc"""
        return web.Response()

    def sync_handler(_):
        """Doc"""
        return web.Response()

    app.router.add_get('/async', async_handler)
    app.router.add_get('/sync', sync_handler)

    for resource in app.router.resources():
        for route in resource:
            assert route.handler.__doc__ == 'Doc'


async def test_unauthorized_folder_access(tmp_dir_path, loop, test_client):
    """
    Tests the unauthorized access to a folder of static file server.
    Try to list a folder content of static file server when server does not
    have permissions to do so for the folder.
    """
    my_dir_path = os.path.join(tmp_dir_path, 'my_dir')
    os.mkdir(my_dir_path)

    app = web.Application()

    with mock.patch('pathlib.Path.__new__') as path_constructor:
        path = MagicMock()
        path.joinpath.return_value = path
        path.resolve.return_value = path
        path.iterdir.return_value.__iter__.side_effect = PermissionError()
        path_constructor.return_value = path

        # Register global static route:
        app.router.add_static('/', tmp_dir_path, show_index=True)
        client = await test_client(app)

        # Request the root of the static directory.
        r = await client.get('/my_dir')
        assert r.status == 403


async def test_access_symlink_loop(tmp_dir_path, loop, test_client):
    """
    Tests the access to a looped symlink, which could not be resolved.
    """
    my_dir_path = os.path.join(tmp_dir_path, 'my_symlink')
    os.symlink(my_dir_path, my_dir_path)

    app = web.Application()

    # Register global static route:
    app.router.add_static('/', tmp_dir_path, show_index=True)
    client = await test_client(app)

    # Request the root of the static directory.
    r = await client.get('/my_symlink')
    assert r.status == 404


async def test_access_special_resource(tmp_dir_path, loop, test_client):
    """
    Tests the access to a resource that is neither a file nor a directory.
    Checks that if a special resource is accessed (f.e. named pipe or UNIX
    domain socket) then 404 HTTP status returned.
    """
    app = web.Application()

    with mock.patch('pathlib.Path.__new__') as path_constructor:
        special = MagicMock()
        special.is_dir.return_value = False
        special.is_file.return_value = False

        path = MagicMock()
        path.joinpath.side_effect = lambda p: (special if p == 'special'
                                               else path)
        path.resolve.return_value = path
        special.resolve.return_value = special

        path_constructor.return_value = path

        # Register global static route:
        app.router.add_static('/', tmp_dir_path, show_index=True)
        client = await test_client(app)

        # Request the root of the static directory.
        r = await client.get('/special')
        assert r.status == 404


async def test_partialy_applied_handler(loop, test_client):
    app = web.Application()

    async def handler(data, request):
        return web.Response(body=data)

    app.router.add_route('GET', '/', functools.partial(handler, b'hello'))
    client = await test_client(app)

    r = await client.get('/')
    data = (await r.read())
    assert data == b'hello'


def test_system_route():
    route = SystemRoute(web.HTTPCreated(reason='test'))
    with pytest.raises(RuntimeError):
        route.url_for()
    assert route.name is None
    assert route.resource is None
    assert "<SystemRoute 201: test>" == repr(route)
    assert 201 == route.status
    assert 'test' == route.reason


async def test_412_is_returned(test_client):

    class MyRouter(abc.AbstractRouter):

        async def resolve(self, request):
            raise web.HTTPPreconditionFailed()

    app = web.Application(router=MyRouter())

    client = await test_client(app)

    resp = await client.get('/')

    assert resp.status == 412


async def test_allow_head(loop, test_client):
    """
    Test allow_head on routes.
    """
    app = web.Application()

    def handler(_):
        return web.Response()
    app.router.add_get('/a', handler, name='a')
    app.router.add_get('/b', handler, allow_head=False, name='b')
    client = await test_client(app)

    r = await client.get('/a')
    assert r.status == 200
    await r.release()

    r = await client.head('/a')
    assert r.status == 200
    await r.release()

    r = await client.get('/b')
    assert r.status == 200
    await r.release()

    r = await client.head('/b')
    assert r.status == 405
    await r.release()
