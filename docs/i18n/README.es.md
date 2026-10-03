<p align="center">
  <img src="../assets/logo.png" alt="Logo de Figure Rebuild" width="128" height="128">
</p>

<h1 align="center">Figure Rebuild</h1>

<p align="center">
  De una figura de referencia a una presentación de PowerPoint editable.
</p>

<p align="center">
  <a href="https://github.com/TH1RT3EN-LI/figure-rebuild/actions/workflows/core-tests.yml"><img src="https://github.com/TH1RT3EN-LI/figure-rebuild/actions/workflows/core-tests.yml/badge.svg?branch=main" alt="Comprobaciones principales"></a>
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/License-MIT-0B6CC2?style=flat" alt="Licencia: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-0B6CC2?style=flat" alt="Python 3.10+">
</p>

<p align="center">
  <a href="../../README.md">简体中文</a> · <a href="README.en.md">English</a> · <a href="README.ko.md">한국어</a> · <strong>Español</strong>
</p>

Figure Rebuild reconstruye diagramas metodológicos de artículos científicos, diagramas de flujo y esquemas de mecanismos en PowerPoint editable, conservando el texto, la disposición y las conexiones de la figura de referencia.

El proyecto se desarrolla y se prueba con Codex. Ofrece una herramienta local de línea de comandos y una habilidad de Codex. La interfaz de línea de comandos también permite integrar otros agentes.

## Vista previa

Ejemplo basado en la figura 1 de [MambaVO (CVPR 2025)](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_MambaVO_Deep_Visual_Odometry_Based_on_Sequential_Matching_Refinement_and_CVPR_2025_paper.html).

**Figura original**

![Figura 1 original del artículo MambaVO](../assets/mambavo-figure1-original.png)

**Proceso de reconstrucción**

![Reconstrucción paso a paso de la figura 1 en el lienzo](../assets/mambavo-figure1-rebuild.gif)

## Contenido editable

| Contenido | Formato de entrega |
| --- | --- |
| Formas y conexiones | Objetos nativos de PowerPoint |
| Texto normal | Cuadros de texto independientes; se pueden cambiar la fuente, el tamaño y el contenido |
| Fórmulas matemáticas | SVG o PNG de alta resolución generados con LaTeX; se pueden regenerar tras modificar el código fuente |
| Fotografías y mapas de calor | Se conservan como imágenes que se pueden mover y recortar |

Se puede exportar un PPTX independiente o insertar la reconstrucción en una presentación existente.

## Inicio rápido

Se necesita Python 3.10+.

```bash
git clone https://github.com/TH1RT3EN-LI/figure-rebuild.git
cd figure-rebuild
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Para usarlo en Codex, instala también la habilidad:

```bash
.venv/bin/python scripts/install.py
```

Antes de exportar un PPTX, hay que [configurar el entorno de exportación](../usage.md#配置和检查): Node.js 20.9+, Codex Artifact Tool, el verificador de Presentations y fuentes locales.

## Uso

El comando `figure-rebuild` permite preparar los recursos, revisar la escena y exportar a PPTX. La interpretación de las imágenes y la creación del manifiesto de escena corresponden al usuario o al agente que invoca la herramienta. Los pasos completos se describen en la [guía de uso](../usage.md).

En Codex, adjunta la figura de referencia e invoca `$figure-rebuild`:

> Usa $figure-rebuild para reconstruir esta figura como un PPT editable. Conserva el texto, la disposición y las conexiones originales, recompón las fórmulas con LaTeX y proporciona una vista previa para que pueda revisarla.

La entrega incluye el PPTX, una vista previa de la exportación y una comparación con la figura original. Puedes seguir ajustando partes concretas del resultado.

## Licencia

El código se distribuye bajo la licencia [MIT](../../LICENSE). Las fuentes y licencias de las figuras de artículos científicos y otros recursos figuran en [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md).
