from pathlib import Path

import cv2
import insightface


_model = None


def get_face_model():
    global _model

    if _model is None:
        _model = insightface.app.FaceAnalysis(
            providers=["CPUExecutionProvider"]
        )
        _model.prepare(
            ctx_id=0,
            det_size=(640, 640),
        )

    return _model


def extract_faces(file_path):
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(path)

    image = cv2.imread(str(path))

    if image is None:
        raise ValueError(f"Unable to read image: {path}")

    faces = get_face_model().get(image)

    results = []

    for face in faces:
        results.append(
            {
                "embedding": face.embedding,
                "bbox": face.bbox.tolist(),
                "det_score": float(face.det_score),
            }
        )

    return results