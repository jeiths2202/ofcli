"""
Protrieve Chunk → Entity 抽出 + MENTIONS 関係作成

基既存 entity_pipeline (batch_extract.py, pattern_extractor.py, neo4j_writer.py) ベース。
Protrieve ドキュメントの Chunk に Entity を接続する。

Usage:
    python scripts/protrieve_entity_extract.py [--dry-run] [--verbose]
"""
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Set

from neo4j import GraphDatabase

# ─── 接続設定 ───
NEO4J_URI = "bolt://192.168.8.11:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "graphrag2024"

BATCH_SIZE = 200


# ─── Entity 抽出結果 ───
@dataclass
class ExtractedEntity:
    name: str
    entity_type: str
    confidence: float
    source: str
    chunk_id: str


# ─── Protrieve + OpenFrame 正規表現パターン ───
# 기존 pattern_extractor.py 에서 계승 + Protrieve 전용 패턴 추가
PATTERNS: Dict[str, List[re.Pattern]] = {
    "command": [
        re.compile(p, re.IGNORECASE)
        for p in [
            # Protrieve statements/commands
            r'\b(?:COPY)\b',
            r'\b(?:FILE|SORT|REPORT|JOB|PARM|PROC|DEFINE|END-PROC)\b',
            r'\b(?:GET|PUT|POINT|DISPLAY|MOVE|SEARCH|DO|END-DO|PERFORM)\b',
            r'\b(?:IF|ELSE|END-IF|GOTO|STOP|PRINT|LIST|READ|WRITE)\b',
            r'\b(?:CALL|RETURN|EXIT|EXECUTE|SQL|END-SQL|SELECT|INSERT|UPDATE|DELETE)\b',
            r'\b(?:SUM|COUNT|AVG|MAX|MIN|TALLY|CONTROL|BREAK-LEVEL|SEQUENCE)\b',
            # *mgr commands
            r'\b[a-z]{2,10}mgr\b',
            # DS tools
            r'\b(?:dsmigin|dsmigout|dsview|dscreate|dsdelete|dscopy)\b',
            # System
            r'\b(?:tmboot|tmdown|ofboot|ofdown)\b',
            # Mainframe utilities
            r'\b(?:IDCAMS|IEBGENER|IEBCOPY|IEFBR14|DFSORT)\b',
        ]
    ],
    "error_code": [
        re.compile(p, re.IGNORECASE)
        for p in [
            # Protrieve error codes (PROTP prefix)
            r'\bPROTP\d{4,6}\b',
            # General error codes
            r'(?<![A-Za-z])-\d{4,5}(?!\d)',
            r'\b[A-Z]{2,10}_ERR_[A-Z_]+\b',
        ]
    ],
    "config": [
        re.compile(p, re.IGNORECASE)
        for p in [
            # Protrieve config
            r'\b(?:PROTBL|PRTCFG|SUMCTL|SUMCTL_JP)\b',
            r'\b(?:DTLCOPY|DTLCOPYALL|DTLCTL|NOALTPRTCOPY|PRTCOPY)\b',
            # OpenFrame config
            r'\b(?:oframe|tjes|hidb|osc|tacf|ds|batch)\.conf\b',
            r'\b(?:OPENFRAME_HOME|TMAX_HOST_ADDR|TB_SID|COBDIR)\b',
        ]
    ],
    "product": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r'\bProTrieve\b',
            r'\bEasytrieve\b',
            r'\bEasytrieve\s+Plus\b',
            r'\bOpenFrame[/ ]?(?:Base|TJES|OSC|TACF|HIDB|ASM|COBOL)\b',
            r'\b(?:OFMiner|OFStudio|OFManager|OFGW)\b',
        ]
    ],
    "technology": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r'\b(?:VSAM|KSDS|ESDS|RRDS|LDS|PDS|GDG)\b',
            r'\b(?:CICS|IMS|DB2|JES2|JES3|TSO|ISPF)\b',
            r'\b(?:COBOL|JCL|REXX|Assembler)\b',
            r'\b(?:SQL|TIBERO|ORACLE|DB2)\b',
        ]
    ],
    "concept": [
        re.compile(p, re.IGNORECASE)
        for p in [
            # Protrieve concepts
            r'\b(?:COPYBOOK|COPYOPER|COPYST)\b',
            r'\b(?:WORK-FILE|WORK\s+FILE|INPUT-FILE|OUTPUT-FILE|PRINT-FILE)\b',
            r'\b(?:SYNCHRONIZED|CONTROLLED)\b',
            r'\b(?:AFTER-BREAK|BEFORE-BREAK|AFTER-LINE|BEFORE-LINE)\b',
            r'\b(?:BREAK-LEVEL|CONTROL|SEQUENCE)\b',
            r'\b(?:ALPHANUMERIC|NUMERIC|PACKED|BINARY|HEX|BWZ)\b',
            r'\b(?:HEADING|DETAIL|TOTAL|FINAL-TOTAL|REPORT-INPUT)\b',
            r'\b(?:RECORD-LENGTH|RECORD-FORMAT|BLOCK-SIZE)\b',
        ]
    ],
}

# 제외할 범용 토큰
STOPWORDS: Set[str] = {
    "the", "this", "that", "with", "from", "for", "and", "not", "are", "was",
    "null", "true", "false", "none", "void", "data", "type", "name", "value",
    "file", "list", "info", "item", "test", "user", "path", "home", "base",
    "read", "get", "put", "if", "do", "end", "job",
}

# カタカナ語抽出 (3文字以上)
KATAKANA_RE = re.compile(r'[ァ-ヶー]{3,}(?:・[ァ-ヶー]{2,})*')
KATAKANA_STOPWORDS: Set[str] = {
    'システム', 'サーバー', 'クライアント', 'ファイル', 'メッセージ',
    'エラー', 'パラメータ', 'プログラム', 'モジュール', 'ライブラリ',
    'アプリケーション', 'ユーザー', 'コマンド', 'オプション',
    'インストール', 'ディレクトリ', 'ガイド', 'マニュアル',
    'ドキュメント', 'セクション', 'バージョン', 'データ',
    'リスト', 'テーブル', 'レコード', 'フィールド',
    'ステータス', 'メソッド', 'プロセス', 'ログ', 'タイプ',
    'サービス', 'リソース',
}


def extract_entities(chunk_id: str, text: str) -> List[ExtractedEntity]:
    """Chunk テキストから Entity 抽出 (パターン + カタカナ)"""
    entities: List[ExtractedEntity] = []
    seen: Set[str] = set()

    # Phase 1: Regex パターンマッチ
    for entity_type, patterns in PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                name = match.group(0).strip()
                norm = name.lower()
                if (
                    norm not in seen
                    and norm not in STOPWORDS
                    and len(name) >= 2
                    and not name.isdigit()
                ):
                    seen.add(norm)
                    entities.append(ExtractedEntity(
                        name=name,
                        entity_type=entity_type,
                        confidence=0.85,
                        source="pattern",
                        chunk_id=chunk_id,
                    ))

    # Phase 2: カタカナ語フォールバック (Phase 1 でゼロの場合のみ)
    if not entities:
        for match in KATAKANA_RE.finditer(text):
            term = match.group(0)
            if term not in seen and term not in KATAKANA_STOPWORDS and len(term) >= 3:
                seen.add(term)
                entities.append(ExtractedEntity(
                    name=term,
                    entity_type="concept",
                    confidence=0.70,
                    source="katakana",
                    chunk_id=chunk_id,
                ))

    return entities


def main():
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv
    start = time.time()

    print("=" * 60)
    print("Protrieve Entity Extraction Pipeline")
    print("=" * 60)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session() as session:
        # ─── Step 1: 처리 전 통계 ───
        r = session.run("""
            MATCH (d:Document)-[:CONTAINS]->(c:Chunk)
            WHERE toLower(d.filename) CONTAINS 'protrieve'
            OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity)
            WITH c, count(e) AS ec
            RETURN count(c) AS total,
                   sum(CASE WHEN ec > 0 THEN 1 ELSE 0 END) AS connected,
                   sum(CASE WHEN ec = 0 THEN 1 ELSE 0 END) AS orphan
        """)
        row = r.single()
        total = row["total"]
        connected_before = row["connected"]
        orphan_before = row["orphan"]
        print(f"\n[Before] Protrieve chunks: {total}")
        print(f"  Connected: {connected_before}")
        print(f"  Orphan:    {orphan_before}")

        if dry_run:
            print("\n  [DRY-RUN] Neo4j 書き込みスキップ")

        # ─── Step 2: Protrieve orphan chunks フェッチ ───
        print(f"\n[Step 2] Orphan chunks fetch...")
        r = session.run("""
            MATCH (d:Document)-[:CONTAINS]->(c:Chunk)
            WHERE toLower(d.filename) CONTAINS 'protrieve'
              AND NOT (c)-[:MENTIONS]->(:Entity)
              AND c.content IS NOT NULL
              AND size(c.content) >= 30
            RETURN c.id AS id, c.content AS content
            ORDER BY c.id
        """)
        chunks = [{"id": rec["id"], "content": rec["content"]} for rec in r]
        print(f"  Fetched: {len(chunks)} orphan chunks")

        # ─── Step 3: Entity 抽出 ───
        print(f"\n[Step 3] Entity extraction...")
        all_entities: List[ExtractedEntity] = []
        no_match = 0

        for chunk in chunks:
            extracted = extract_entities(chunk["id"], chunk["content"])
            if extracted:
                all_entities.extend(extracted)
            else:
                no_match += 1

        # 통계
        type_counts: Dict[str, int] = {}
        for e in all_entities:
            type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1

        print(f"  Total entities extracted: {len(all_entities)}")
        print(f"  Chunks with no match:    {no_match}")
        print(f"  Entity type distribution:")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"    {t}: {c}")

        if verbose:
            # Unique entity names
            unique = {}
            for e in all_entities:
                if e.name not in unique:
                    unique[e.name] = e.entity_type
            print(f"\n  Unique entities ({len(unique)}):")
            for name, etype in sorted(unique.items()):
                print(f"    [{etype}] {name}")

        # ─── Step 4: Neo4j MERGE ───
        if not dry_run and all_entities:
            print(f"\n[Step 4] Writing to Neo4j...")

            # 중복 제거 (같은 name + chunk_id)
            unique_items = {}
            for e in all_entities:
                key = (e.name.lower(), e.chunk_id)
                if key not in unique_items or e.confidence > unique_items[key]["confidence"]:
                    unique_items[key] = {
                        "name": e.name,
                        "type": e.entity_type,
                        "confidence": e.confidence,
                        "chunk_id": e.chunk_id,
                    }

            items = list(unique_items.values())
            total_written = 0

            for i in range(0, len(items), BATCH_SIZE):
                batch = items[i:i + BATCH_SIZE]
                r = session.run("""
                    UNWIND $batch AS item
                    MERGE (e:Entity {name: item.name})
                      ON CREATE SET
                        e.type = item.type,
                        e.confidence = item.confidence,
                        e.source = 'protrieve_pipeline',
                        e.created_at = datetime()
                      ON MATCH SET
                        e.confidence = CASE
                          WHEN item.confidence > e.confidence
                          THEN item.confidence
                          ELSE e.confidence END
                    WITH e, item
                    MATCH (c:Chunk {id: item.chunk_id})
                    MERGE (c)-[:MENTIONS]->(e)
                    RETURN count(*) AS cnt
                """, batch=batch)
                cnt = r.single()["cnt"]
                total_written += cnt
                print(f"  Batch {i // BATCH_SIZE + 1}: {len(batch)} items -> {cnt} MENTIONS")

            print(f"  Total written: {total_written}")

        # ─── Step 5: 처리 후 통계 ───
        print(f"\n[Step 5] After stats...")
        r = session.run("""
            MATCH (d:Document)-[:CONTAINS]->(c:Chunk)
            WHERE toLower(d.filename) CONTAINS 'protrieve'
            OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity)
            WITH c, count(e) AS ec
            RETURN count(c) AS total,
                   sum(CASE WHEN ec > 0 THEN 1 ELSE 0 END) AS connected,
                   sum(CASE WHEN ec = 0 THEN 1 ELSE 0 END) AS orphan
        """)
        row = r.single()
        connected_after = row["connected"]
        orphan_after = row["orphan"]

        print(f"\n{'=' * 60}")
        print("RESULT SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Chunks:    {total}")
        print(f"  Connected: {connected_before} -> {connected_after}")
        print(f"  Orphan:    {orphan_before} -> {orphan_after}")
        print(f"  New MENTIONS: +{connected_after - connected_before}")
        print(f"  Time: {time.time() - start:.1f}s")

    driver.close()


if __name__ == "__main__":
    main()
