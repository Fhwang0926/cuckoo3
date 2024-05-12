import os
import re
from jinja2 import Environment, FileSystemLoader, Undefined

# 템플릿 파일이 있는 디렉토리 경로
template_dir = 'd:\\git\\cuckoo3\\web\\cuckoo\\web\\templates'  # 실제 경로로 변경 필요

class SilentUndefined(Undefined):
  """
  에러 발생시 조용히 처리하고, 대신 None을 반환하는 Undefined의 서브클래스
  """
  def _fail_with_undefined_error(self, *args, **kwargs):
      # 이 메서드에서 에러를 로깅하거나 기본값을 반환하도록 커스텀 로직을 추가할 수 있습니다.
      print(f"Warning: Undefined variable or filter used: {self}")
      return None
    
# Jinja2 환경 설정
env = Environment(loader=FileSystemLoader(template_dir), undefined=SilentUndefined)

# 모든 Jinja2 템플릿 파일 순회
extracted_texts = []

# 사용자 정의 필터 정의
def feature_enabled(feature_name):
  # 예시: 간단한 기능 활성화 상태 딕셔너리
  features = {
      'search': True,
      'analytics': False
  }
  return features.get(feature_name, False)

# URL 생성 함수 정의
def url(endpoint):
  # 여기에 실제 URL 생성 로직 구현
  return 'web\\cuckoo\\web\\static' + endpoint

# static 함수 정의
def static(file_path):
  return 'd:\\git\\cuckoo3\\web\\cuckoo\\web\\static' + file_path

env.filters['feature_enabled'] = feature_enabled


for root, dirs, files in os.walk(template_dir):
    for file in files:
      if "yaml" in file:
        continue
      
      if file.endswith('.jinja2'):  # Jinja2 파일 확장자로 필터링
          # 템플릿 불러오기
          # root.split(template_dir)
          relative_path = f"{template_dir}\\{os.path.relpath(os.path.join(root, file), start=template_dir)}"
          print(f"Processing template: {relative_path}")  # 처리 중인 파일 경로 출력
          template = None
          try:
            # 템플릿 로드 및 렌더링
            template = env.get_template(relative_path)
            rendered_template = template.render(url=url, static=static)  # 컨텍스트 변수 제공
            # HTML 태그 사이의 텍스트 추출 작업 수행
          except Exception as e:
            print(f"Error rendering template {relative_path}: {e}")
            rendered_template = open(relative_path, encoding="utf-8").read()
          
          # HTML 태그 사이의 텍스트 추출
          found_texts = re.findall(r'>([^<]+)<', rendered_template)
          if found_texts:
            if "%" in found_texts:
              continue
            extracted_texts.extend(found_texts)

# 결과 출력
for text in extracted_texts:
  print(text)
