# Linux WPS 字体交付后续

2026-10-07；用户将本轮实际验收范围收敛到 Linux。整体台账仍为原296项全部关闭、后续83项82关闭/1项主要问题开放；用户最终验收 pending，尚未合并 main。

最终本地交付 `linux-delivery-040` 含49份原生PPTX（原48份加ColBERT）、300个使用字体角色、可撤回安装器和49页实际WPS预览。Linux WPS 12.1.2.26885 忽略或替换原 EOT 的隔离导入失败已经保留，本版使用随包安装字体；没有宣称无字体依赖。唯一字体别名避免不同源子集在同一family/style下互相覆盖；两个有确切源墨迹对应的完整Type1字体恢复空格及可用字符。

49份全部以可编辑方式打开，300个角色分别改字、保存和重开；55个角色使用了原图未用但字体确实含有的字符。实际保存PPTX的Unicode文字49/49与预期一致。接口字符串比较48/49的失败保留：一份非BMP字符被RPC附加NUL，独立XML核验才确认实际文本正确。

改字前后98份实际PDF、39,421个绘制字形，全部角色通过真实嵌入程序及原生字体句柄/有限轮廓核对，未见替换字体或合成粗体/斜体。此前三份的9个可见Arial替换也已恢复。49包只变登记别名和WPS所需文字格式，其他包部件、几何、媒体、对象顺序及文字Unicode均保留；300字体保留原权限和旧非空cmap字形墨迹/advance。

Swift原三个普通字母槽为供GSUB连字的空组件，WPS未执行对应连字，导致缺字。已用Google Sans v14.000 OFL的regular f、bold f/t补入旧空槽；新hint指令移除，旧其他墨迹、GSUB和权限保留。此为新独立组件近似，未宣称原连字墨迹相同。历史v3.002候选因缺相应许可排除。新字体只对新组件添加版权/许可说明，没有把原子集重新标为OFL。冻结重放程序逐字节重现018两字体，并重现最终023的300字体及49原生稿。

032为023的版权元信息后继：独审发现023新组件署名错误，按实际v14 donor的name-ID-0改为2025 The Google Sans Project Authors。原OFL侧文件的2022 Google Sans Flex记录原字节保留并单列，不混称字体内部署名。只改两字体name-ID-0，其他表除name/head外、其余298字体和49原生稿字节相同；生成器可重放。更正后Swift再次实际编辑三个角色并核对两PDF的402字形，1/2/4倍显示与已查看025版本完全相同。49页预览用新Swift实际导出替换对应页，逐页1倍RGBA仍准确对应最终来源。WPS导出子集省略name表，未宣称署名随PDF字体元数据保留。旧错署名及Qt结束阶段失败记录不覆盖。

已复看全部49图总览和Swift及三张局部修复图的1/2/4倍结果。SAM白色接缝、InstructGPT独立圆框和ColBERT三处蓝色裁边修复仍有效。WPS字号、字距和基线经实际导出校正；相对冻结LibreOffice参考的非连字续位字符起点最大观察差约1.758pt，字号最大差约0.160pt。原稿已经截断的上下文按原边界保留；未声明跨应用整图像素相同。

`ccf-2020-14-f03`的原生数学Unicode正确，但WPS导出ToUnicode仍把部分非BMP字符映为U+FFFD；有限墨迹匹配不能恢复PDF文本语义。完整家族/任意新增字符、原子集再分发许可、hinting、Windows/macOS和用户验收继续保留，因此字体主要问题不整体结案。原始字体只在本地交付，未发布到Git。

[Linux交付说明](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-editing-20261007/linux-delivery-040/README.md) · [49页实际WPS预览](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-editing-20261007/linux-delivery-040/Linux-WPS-49-figures-preview.pdf) · [最终结果与回执哈希](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-editing-20261007/linux-delivery-040/RESULT.json)

当前040把逐字体新增范围进一步精确写入署名：regular只新增f，bold新增f/t。相对032仅regular的name-ID-0语义更正，299字体与49原生稿字节未变；再次WPS编辑/重开及1/2/4倍像素一致检查通过，完整重放和安装哈希读回均保留。
