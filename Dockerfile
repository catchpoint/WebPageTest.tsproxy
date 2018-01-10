FROM python:2.7

COPY ./tsproxy.py /usr/src/tsproxy/
WORKDIR /usr/src/tsproxy/
RUN chmod -v a+x ./tsproxy.py

ENTRYPOINT [ "./tsproxy.py", "--bind", "0.0.0.0" ]
