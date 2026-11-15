"""Read the editor-rendered image without desktop focus or screen scraping."""
from .drawing_api import _IDENTITY

IMAGE_QUERY=_IDENTITY+r"""
const before=await stable();
if(typeof eda.dmt_EditorControl.getCurrentRenderedAreaImage!=='function')throw new Error('当前版本未提供画布图像API');
const blob=await eda.dmt_EditorControl.getCurrentRenderedAreaImage(before.identity.tab_id);
if(!blob||blob.size>15000000)throw new Error('图像不存在或过大');
const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';
for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.slice(i,i+16384));
const after=await stable(before.identity);if(comparable(before.source)!==comparable(after.source))throw new Error('截图期间源变化');
return {image_base64:btoa(binary),mime:blob.type,size:blob.size,identity:before.identity};
"""
