"""Conversion subprocess; launched with a private temporary directory and timeout."""
import os,subprocess,sys
from pathlib import Path

def main():
    mode,folder=sys.argv[1:];folder=Path(folder)
    if mode=='pdf_to_word':
        from pdf2docx import Converter
        cv=Converter(str(folder/'source.pdf'))
        try:cv.convert(str(folder/'result.docx'),multi_processing=False)
        finally:cv.close()
    elif mode=='word_to_pdf':
        profile=folder/'profile';profile.mkdir()
        (profile/'user').mkdir()
        (profile/'user/registrymodifications.xcu').write_text('''<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item><item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item></oor:items>''')
        subprocess.run(['libreoffice','-env:UserInstallation='+profile.as_uri(),'--headless','--nologo','--nodefault','--norestore','--convert-to','pdf:writer_pdf_Export','--outdir',str(folder),str(folder/'source.docx')],check=True,env={**os.environ,'HOME':str(folder)})
        (folder/'source.pdf').rename(folder/'result.pdf')
    else:raise ValueError('Unsupported conversion')
if __name__=='__main__':main()
