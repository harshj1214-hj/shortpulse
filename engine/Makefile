CC      ?= gcc
CFLAGS  ?= -O2 -Wall -Wextra -std=c11 -fPIC
LDLIBS  = -lpthread

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
  LIB = libshortpulse.dylib
  SHARED = -dynamiclib
else
  LIB = libshortpulse.so
  SHARED = -shared
endif

.PHONY: all test clean

all: $(LIB)

$(LIB): shortpulse_engine.c shortpulse_engine.h
	$(CC) $(CFLAGS) $(SHARED) -o $@ shortpulse_engine.c $(LDLIBS)

test_engine: test_engine.c shortpulse_engine.c shortpulse_engine.h
	$(CC) $(CFLAGS) -o $@ test_engine.c shortpulse_engine.c $(LDLIBS)

test: test_engine
	./test_engine

clean:
	rm -f $(LIB) test_engine
