"""Storage API endpoint paths shared by synchronous and asynchronous clients."""

FILE_SHARE_CREATE_URL = "/storage/file_share"
FILE_SHARE_LIST_URL = "/storage/file_share/list"
FILE_SHARE_GET_URL = "/storage/file_share/{biz_no}"
FILE_SHARE_CANCEL_URL = "/storage/file_share/{biz_no}/cancel"
FILE_SHARE_DELETE_URL = "/storage/file_share/delete"

TRANSFER_SUBMIT_URL = "/storage/transfer"
TRANSFER_LIST_URL = "/storage/transfer/list"
TRANSFER_CANCEL_URL = "/storage/transfer/{transfer_id}/cancel"
TRANSFER_DELETE_URL = "/storage/transfer/{transfer_id}"
TRANSFER_CLEAR_URL = "/storage/transfer/clear"
