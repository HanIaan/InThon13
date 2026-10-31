"""S_DOT_ENV 주간 CSV를 백엔드 ingestion이 읽는 S-DoT_NATURE 형식으로 바꾼다.

사용법: python3 tools/convert_sdot_weekly.py <입력.csv> [...] data/raw/seoul/sdot
헤더 라벨만 바꾸고 59번째 열을 뺀다. 측정값은 건드리지 않는다.
"""
import csv
import os
import re
import sys

ENCODING = "cp949"
COLUMNS = 58
HEADER_LABELS = {
    2: "측정시간",
    18: "조도 최대",
    19: "조도 평균",
    20: "조도 최소",
    24: "소음 최대",
    25: "소음 평균",
    26: "소음 최소",
}


def convert(source, out_dir):
    name = re.sub(r"^S_DOT_ENV_", "S-DoT_NATURE_", os.path.basename(source))
    target = os.path.join(out_dir, name)
    with open(source, encoding=ENCODING, newline="") as reader_file, open(target, "w", encoding=ENCODING, newline="") as writer_file:
        reader = csv.reader(reader_file)
        writer = csv.writer(writer_file, lineterminator="\r\n")
        header = next(reader)[:COLUMNS]
        for index, label in HEADER_LABELS.items():
            header[index] = label
        writer.writerow(header)
        rows = 0
        for row in reader:
            writer.writerow(row[:COLUMNS])
            rows += 1
    print(name, rows)


if __name__ == "__main__":
    *sources, out_dir = sys.argv[1:]
    os.makedirs(out_dir, exist_ok=True)
    for source in sources:
        convert(source, out_dir)
