<p align="center">
  <img src="../assets/logo.png" alt="Figure Rebuild logo" width="128" height="128">
</p>

<h1 align="center">Figure Rebuild</h1>

<p align="center">
  참고 그림을 편집 가능한 PowerPoint로.
</p>

<p align="center">
  <a href="https://github.com/TH1RT3EN-LI/figure-rebuild/actions/workflows/core-tests.yml"><img src="https://github.com/TH1RT3EN-LI/figure-rebuild/actions/workflows/core-tests.yml/badge.svg?branch=main" alt="Core checks"></a>
  <!-- Official count badge when install statistics become available: https://skills.sh/b/TH1RT3EN-LI/figure-rebuild -->
  <a href="https://skills.sh/th1rt3en-li/figure-rebuild"><img src="https://img.shields.io/badge/skills.sh-install-111111" alt="Install with skills.sh"></a>
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/License-MIT-0B6CC2?style=flat" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-0B6CC2?style=flat" alt="Python 3.10+">
</p>

<p align="center">
  <a href="../../README.md">简体中文</a> · <a href="README.en.md">English</a> · <strong>한국어</strong> · <a href="README.es.md">Español</a>
</p>

Figure Rebuild는 논문의 방법론 도식, 흐름도, 작동 원리 도식을 편집 가능한 PowerPoint로 다시 그리는 도구입니다. 참고 그림의 텍스트, 레이아웃, 연결 관계를 유지합니다.

Codex를 사용해 개발하고 테스트했으며, 로컬 명령줄 도구와 Codex 스킬을 제공합니다. 명령줄 인터페이스는 다른 에이전트와 연동하는 데도 사용할 수 있습니다.

## 스킬 설치

사용할 프로젝트에서 실행합니다:

```bash
npx skills add TH1RT3EN-LI/figure-rebuild
```

안내에 따라 Codex를 선택하세요. 모든 프로젝트에서 사용하려면 `npx skills add TH1RT3EN-LI/figure-rebuild --agent codex --global`을 실행합니다. 스킬, 스크립트, 참고 문서를 설치하며, Python 의존성과 PPT 내보내기 환경은 [사용 가이드](../usage.md#安装)에 따라 설정해야 합니다.

## 결과 미리 보기

아래 예시는 [MambaVO(CVPR 2025)](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_MambaVO_Deep_Visual_Odometry_Based_on_Sequential_Matching_Refinement_and_CVPR_2025_paper.html)의 Figure 1을 사용합니다.

**원본**

![MambaVO 논문의 Figure 1 원본](../assets/mambavo-figure1-original.png)

**재구성 과정**

![Figure 1의 캔버스를 단계적으로 재구성하는 과정](../assets/mambavo-figure1-rebuild.gif)

## 편집 가능한 요소

| 요소 | 제공 형식 |
| --- | --- |
| 도형 및 연결선 | PowerPoint 네이티브 개체 |
| 일반 텍스트 | 독립 텍스트 상자. 글꼴, 글꼴 크기, 내용 수정 가능 |
| 수학 수식 | LaTeX로 생성한 SVG / 고해상도 PNG. 소스를 수정한 뒤 다시 조판 |
| 사진 및 히트맵 | 이미지로 유지하며 이동 및 자르기 가능 |

독립된 PPTX로 내보내거나 재구성한 결과를 기존 프레젠테이션에 삽입할 수 있습니다.

## 빠른 시작

Python 3.10 이상이 필요합니다.

```bash
git clone https://github.com/TH1RT3EN-LI/figure-rebuild.git
cd figure-rebuild
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

이미 소스 체크아웃이 있다면 로컬 설치기로 Codex 스킬을 등록할 수도 있습니다:

```bash
.venv/bin/python scripts/install.py
```

PPTX를 내보내려면 [내보내기 환경을 설정](../usage.md#配置和检查)해야 합니다. Node.js 20.9 이상, Codex Artifact Tool, Presentations 검사 도구와 로컬 글꼴이 필요합니다.

## 사용법

`figure-rebuild` 명령은 입력 자료 준비, 장면 검토, PPTX 내보내기를 제공합니다. 이미지 해석과 장면 명세 작성은 사용자 또는 도구를 호출하는 에이전트가 담당합니다. 전체 절차는 [사용 가이드](../usage.md)를 참고하세요.

Codex에서는 참고 그림을 첨부하고 `$figure-rebuild`를 호출할 수 있습니다:

> $figure-rebuild를 사용해 이 그림을 편집 가능한 PPT로 다시 그려 주세요. 원본 텍스트, 레이아웃, 연결선을 유지하고 수식은 LaTeX로 다시 조판해 주세요. 확인할 수 있도록 미리 보기도 제공해 주세요.

출력에는 PPTX, 내보낸 결과의 미리 보기, 원본과의 비교가 포함되며, 이후 원하는 부분을 수정할 수 있습니다.

## 라이선스

코드는 [MIT](../../LICENSE) 라이선스로 배포됩니다. 논문 그림과 기타 자료의 출처 및 라이선스는 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)를 참고하세요.
