"""GET /v1/products — Supported product list"""
from fastapi import APIRouter

from api.models.response import ProductInfo, ProductsResponse
from app.agents.query_agent import PRODUCT_KEYWORDS

router = APIRouter(prefix="/v1", tags=["products"])

_PRODUCT_NAMES = {
    "mvs_openframe_7.1": "MVS OpenFrame 7.1",
    "openframe_hidb_7": "OpenFrame HiDB 7 (IMS)",
    "openframe_osc_7": "OpenFrame OSC 7 (CICS)",
    "tibero_7": "Tibero 7",
    "ofasm_4": "OFASM 4",
    "ofcobol_4": "OFCOBOL 4",
    "tmax_6": "Tmax 6",
    "jeus_8": "JEUS 8",
    "webtob_5": "WebtoB 5",
    "ofstudio_7": "OFStudio 7",
    "protrieve_7": "Protrieve 7",
    "xsp_openframe_7": "XSP OpenFrame 7 (Fujitsu)",
}


@router.get("/products", response_model=ProductsResponse)
async def list_products():
    products = [
        ProductInfo(
            id=pid,
            name=_PRODUCT_NAMES.get(pid, pid),
            keywords=cfg["keywords"],
        )
        for pid, cfg in PRODUCT_KEYWORDS.items()
    ]
    return ProductsResponse(products=products)
