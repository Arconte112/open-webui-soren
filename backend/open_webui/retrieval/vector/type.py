try:
    from enum import StrEnum
except ImportError:  # Py3.10 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class VectorType(StrEnum):
    MILVUS = "milvus"
    QDRANT = "qdrant"
    CHROMA = "chroma"
    PINECONE = "pinecone"
    ELASTICSEARCH = "elasticsearch"
    OPENSEARCH = "opensearch"
    PGVECTOR = "pgvector"
    ORACLE23AI = "oracle23ai"
    S3VECTOR = "s3vector"
    WEAVIATE = "weaviate"
    OPENGAUSS = "opengauss"
