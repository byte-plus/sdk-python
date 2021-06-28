import datetime
import gzip
import hashlib
import logging
import random
import string
import time
import uuid
from typing import Optional

import requests
from google.protobuf.message import Message
from requests import Response

from byteplus.core.context import Context
from byteplus.core.exception import NetException, BizException
from byteplus.core.option import Option
from byteplus.core.options import _Options

log = logging.getLogger(__name__)

_SUCCESS_HTTP_CODE = 200


class HttpCaller(object):

    def __init__(self, context: Context):
        self._context = context

    def do_request(self, url: str, request: Message, response: Message, *opts: Option):
        req_bytes: bytes = gzip.compress(request.SerializeToString())
        options: _Options = Option.conv_to_options(opts)
        headers: dict = self._build_headers(options, req_bytes)
        rsp_bytes = self._do_http_request(url, headers, req_bytes, options.timeout)
        if rsp_bytes is not None:
            try:
                response.ParseFromString(rsp_bytes)
            except BaseException as e:
                log.error("[ByteplusSDK] parse response fail, err:%s url:%s", e, url)
                raise BizException("parse response fail")

    def _build_headers(self, options: _Options, req_bytes: bytes) -> dict:
        headers = {
            "Content-Encoding": "gzip",
            # The 'requests' lib support '"Content-Encoding": "gzip"' header,
            # it will decompress gzip response without us
            "Accept-Encoding": "gzip",
            "Content-Type": "application/x-protobuf",
            "Accept": "application/x-protobuf",
        }
        if options.request_id is not None and len(options.request_id) > 0:
            headers["Request-Id"] = options.request_id
        else:
            headers["Request-Id"] = str(uuid.uuid1())
        self._with_auth_headers(headers, req_bytes)
        return headers

    def _with_auth_headers(self, headers: dict, req_bytes: bytes) -> None:
        # 获取当前时间不带小数的秒级时间戳
        ts = str(int(time.time()))
        # 生成随机字符串。取8字符即可，太长会浪费
        # 为节省性能，也可以直接使用`ts`作为`nonce`
        nonce = ''.join(random.sample(string.ascii_letters + string.digits, 8))
        signature = self._cal_signature(req_bytes, ts, nonce)

        headers['Tenant-Id'] = self._context.tenant_id
        headers['Tenant-Ts'] = ts
        headers['Tenant-Nonce'] = nonce
        headers['Tenant-Signature'] = signature
        return

    def _cal_signature(self, req_bytes: bytes, ts: str, nonce: str) -> str:
        # 按照token、httpBody、tenantId、ts、nonce的顺序拼接，顺序不能搞错
        # 本身为字符串的字段，需要使用utf-8方式编码
        # http_body_bytes本身为bytes类型，因此无需编码
        sha256 = hashlib.sha256()
        sha256.update(self._context.token.encode('utf-8'))
        sha256.update(req_bytes)
        sha256.update(self._context.tenant_id.encode('utf-8'))
        sha256.update(ts.encode('utf-8'))
        sha256.update(nonce.encode('utf-8'))
        # 生成16进制的sha256 hash码
        return sha256.hexdigest()

    def _do_http_request(self, url: str, headers: dict,
                         req_bytes: bytes, timeout: Optional[datetime.timedelta]) -> Optional[bytes]:
        start = time.time()
        try:
            if timeout is not None:
                timeout_secs = timeout.total_seconds()
                rsp: Response = requests.post(url=url, headers=headers, data=req_bytes, timeout=timeout_secs)
            else:
                rsp: Response = requests.post(url=url, headers=headers, data=req_bytes)
        except BaseException as e:
            if "timeout" in str(e).lower():
                log.error("[ByteplusSDK] do http request timeout, msg:%s", e)
                raise NetException(str(e))
            log.error("[ByteplusSDK] do http request occur io exception, msg:%s", e)
            raise BizException(str(e))
        finally:
            cost = int((time.time() - start) * 1000)
            log.debug("[ByteplusSDK] http path:%s, cost:%dms", url, cost)
        if rsp.status_code != _SUCCESS_HTTP_CODE:
            self._log_rsp(url, rsp)
            raise NetException("code:%d msg:%s".format(rsp.status_code, rsp.reason))
        return rsp.content

    @staticmethod
    def _log_rsp(url: str, rsp: Response) -> None:
        rsp_bytes = rsp.content
        if rsp_bytes is not None and len(rsp.content) > 0:
            log.error("[ByteplusSDK] http status not 200, url:%s code:%d msg:%s body:%s",
                      url, rsp.status_code, rsp.reason, str(rsp_bytes))
        else:
            log.error("[ByteplusSDK] http status not 200, url:%s code:%d msg:%s",
                      url, rsp.status_code, rsp.reason)
        return