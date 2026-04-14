"""CT-OS V4.0 数据湖管理 API"""

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/overview")
async def lake_overview():
    """数据湖概览: 已缓存股票、各周期条数、磁盘占用"""
    from server.services.lake_meta import scan_lake_overview
    return scan_lake_overview()


@router.post("/cleanup")
async def cleanup_orphans():
    """清理孤儿 WAL/SHM 文件"""
    from server.services.lake_meta import cleanup_orphan_files
    return cleanup_orphan_files()


@router.delete("/{symbol}")
async def delete_stock_data(symbol: str, freq: str = Query(None)):
    """删除指定股票的缓存数据。freq=None 时删除全部周期"""
    from server.services.lake_meta import delete_stock_data as do_delete
    result = do_delete(symbol, freq)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=result.get("detail", "删除失败"))
    return result


@router.get("/sync-status")
async def sync_status():
    """获取同步元信息"""
    from server.services.lake_meta import get_sync_status
    return get_sync_status()
