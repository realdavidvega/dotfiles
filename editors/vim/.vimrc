if filereadable(expand('~/.space-vim/init.vim'))
  execute 'source' fnameescape(expand('~/.space-vim/init.vim'))
endif

if system('uname -s') == "Darwin\n"
  set clipboard=unnamed "OSX
else
  set clipboard=unnamedplus "Linux
endif
