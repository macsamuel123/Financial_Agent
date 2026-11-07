import requests
import streamlit as st

API_URL="http://localhost:8000"

st.title("TFSA Assistant")

try:
    members=requests.get(f"{API_URL}/members").json()
except requests.exceptions.RequestException:
    st.error("Can't load members. Is the API running?")
    st.stop()

options = {"No member (general questions)": None}

for m in members:
    options[m["first_name"]]=m["member_id"]

choice=st.selectbox("Member",list(options.keys()))

member_id=options[choice]

question=st.text_input("Ask a question")

if st.button("Ask"):
    with st.spinner("Thinking...."):
        if not question:
            st.warning("Type a question first")
            st.stop()
        else:
            try:
             response = requests.post(f"{API_URL}/ask",
                         json={"question": question, "member_id": member_id},
                         timeout=120)
            except requests.exceptions.RequestException:
                st.error("Can't reach the assistant right now. Please try again shortly.")
                st.stop()

    if response.status_code!=200:
        st.error(f"Something went wrong (status{response.status_code})")
        st.stop()

    data=response.json()
    category=data["category"]

    if category == "safe":
        st.success(f"Category: {category}")
    elif category == "violation":
        st.error(f"Category: {category}")
    else:
        st.info(f"Category: {category}")
    
    st.write(data["answer"])

    if len(data["citations"])>0:
        st.subheader("Sources")
        for c in data["citations"]:
            st.write(f"Page {c['page']}: *{c['quote']}*")
    if data["needs_review"]:
        st.warning("Flagged for human review")