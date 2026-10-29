# Image for `python -m evals.history_real_repo --python docker:opspulse-history-tests`.
#   docker build -t opspulse-history-tests -f evals/history_tests.Dockerfile evals
# The tests run with no network, so every test dependency of the repositories under evaluation must be in the image.
FROM python:3.11-slim
RUN pip install --no-cache-dir pytest hypothesis freezegun pytz python-dateutil wcwidth typing_extensions \
    text-unidecode mdurl
