
foo = 22

PASS = echo 'PASS' | grep 'PASS'
FAIL = echo 'FAIL' | grep 'PASS'

all: testIfDef testIfnDef testEql testNeql testNested testStacked testChain testAltSyntax
ifneq '1' '1'
#	$(PASS)
	$(FAIL)
endif

testIfDef:
ifdef foo
	$(PASS)
else
	$(FAIL)
endif

testIfnDef:
ifndef foo
	$(FAIL)
else
	$(PASS)
endif
ifndef bar
	$(PASS)
endif
ifdef bar
	$(FAIL)
endif

testEql:
ifeq 'a' 'a'
	$(PASS)
else
	$(FAIL)
endif
ifeq ($(strip $(foo)),22)
	$(PASS)
else
	$(FAIL)
endif

ifneq "$(PATH)" "/not the path..."
testNeql: testNEqlWithSpaces
endif

 ifneq    "a" "a     "
testNEqlWithSpaces:
   endif

ifeq "$(PASS)" "$(PASS)"
   ifeq "$(PASS)" "$(FAIL)"
   else
 ifneq (1, 2)
testNested:
    endif
   endif
endif

ifdef MAKE
testStacked:
endif
	echo 1 > .test_file_A
ifdef foo
	cat .test_file_A | grep 1
	rm .test_file_A
endif
	$(PASS)

testAltSyntax:
	echo "ifeq (foo, bar) syntax."
ifeq (f,foo)
	$(FAIL)
else ifeq (,)
	$(PASS)
else
	$(FAIL)
endif
ifneq (a,a)
	$(FAIL)
endif
ifneq ($(PASS), $(PASS))
	$(FAIL)
endif

testChain:
	echo "START OF CHAIN......"
ifndef foo
	echo $(foo); $(FAIL) # ifndef foo
else ifdef bar
	$(FAIL) # else ifdef bar
else ifeq '1' '1'
	echo "Testchain:"; $(PASS) # else ifeq '1' '1'
  ifeq (2, 2)
 ifneq (2, 2)
  else ifneq (3, 3)
  else
		echo 66 > .test_file_AB
 endif
  endif
else
	$(FAIL) # else
endif
	echo "END OF CHAIN............"
	cat .test_file_AB | grep 66
	rm .test_file_AB
