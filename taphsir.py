import artifact.utils, importlib, sys, spacy
importlib.reload(sys.modules['artifact.utils'])
from artifact.utils import *
import argparse

def main():
    parser = argparse.ArgumentParser(description='anaphora')
    parser.add_argument('--doc', dest='doc', type=str, default="Example.txt", help='Path to the input txt requirements document. The requirements should be seperated by line breaks.')
    parser.add_argument('--mode', dest='mode', type=int, default=3, help='Mode selection: 1 for detection, 2 for resolution, and 3 for both. Default value=3')
    parser.add_argument('--detection', dest='dfeatures', type=str, default="Ensemble", help='Detection features: LF for language features, FE for features embedding, and Ensemble for both. Default value=Ensemble')
    args = parser.parse_args()
    nlp = spacy.load("en_core_web_sm")
    df=preprocess(args.doc,nlp)
    if not os.path.isdir("output"):
        os.makedirs("output")
    if args.mode == 1:
        ddf=detection(df,args.dfeatures,nlp)
        ddf.to_excel("output/detection.xlsx")
    elif args.mode == 2:
        rdf=resolution(df,nlp)
        rdf.to_excel("output/resolution.xlsx")
    elif args.mode == 3:        
        ddf=detection(df,args.dfeatures,nlp)
        ddf.to_excel("output/detection.xlsx",index=False)
        rdf=resolution(df,nlp)
        rdf.to_excel("output/resolution.xlsx",index=False)

def detection(df,features,nlp):
    final_pred,X= None, None
    if features=="LF":
        final_pred,X =getLFpred(df,nlp)

    elif features=="FE":
        final_pred, X=getFEpred(df)
    else:
        lfpred, X=getLFpred(df,nlp)
        fepred, _=getFEpred(df)
        final_pred=ensembleprobaN(lfpred,fepred,theta=0.1)
    detdf=getprediction(X.drop('Id',axis=1).index,final_pred,X.Id,0.5,df).drop_duplicates(subset=['Id'])
    return detdf

def getLFpred(df,nlp):
    nlp.add_pipe('benepar', config={'model': 'benepar_en3'})
    nlp1 = stanza.Pipeline('en')
    df=extract_LF(df,nlp1)
    X=df.drop(["sents","Context","Pronoun","Candidate Antecedent"],axis=1)
    X.isNextVerbAnimate=X.isNextVerbAnimate.astype(bool)
    object_cols = []
    to_remove=['Id']
    for col, types in zip(
            X.dtypes.index,
            X.dtypes):
        if types == object:
            if len(pd.unique(X[col]))<30:
                object_cols.append(col)
            else:
                to_remove.append(col)
    X=X.drop(to_remove,axis=1)
    X=pd.get_dummies(X,columns=object_cols[1:])
    trainCols=loadObj("artifact/trainingCols.list")
    X=X.fillna(value=0)
    for col in list(X.columns):
        if col not in trainCols:
            X.drop(col,axis=1,inplace=True)
    for col in trainCols:
        if col not in X.columns:
            X[col]=0
    X['Id']=df['Id']
    ML_LF_Detection=loadObj("artifact/ML_LF-detection.Anaphora")
    ML_LF_D_predictions=ML_LF_Detection.predict_proba(X.drop('Id',axis=1))
    return ML_LF_D_predictions, X

def getFEpred(df):
    tokenizer = transformers.BertTokenizer.from_pretrained('bert-base-cased')
    model = transformers.BertModel.from_pretrained('bert-base-cased',output_hidden_states = True)
    Hs4v = df.apply( lambda x: get_4layers_emb(hashdouble(x['Context'],x['Pronoun'],x['Candidate Antecedent']).strip() + " [SEP] " + x['Pronoun'].text +
                               "#1 [SEP] " + x['Candidate Antecedent'].text+"#2",tokenizer,model,concat=False),axis=1)
    Hs4=Hs4v.apply(lambda s: pd.Series({i: float(s[i]) for i in range(0, len(Hs4v[Hs4v.index[0]]))}))
    Hs4["Id"]=df["Id"]
    ML_FE_Detection=loadObj("artifact/ML_FE-detection.Anaphora")
    ML_FE_D_predictions=ML_FE_Detection.predict_proba(Hs4.drop("Id",axis=1))
    return ML_FE_D_predictions, Hs4

def resolution(df,nlp):
    fast_tokenizer = BertTokenizerFast.from_pretrained('SpanBERT/spanbert-base-cased')
    remodel = BertForTokenClassification.from_pretrained('artifact/SpanBERT-REv21.9.01')
    test=[]
    for Id in df.Id.unique():
        c=df[df.Id==Id].Context.unique()[0]
        pronoun=df[df.Id==Id].Pronoun.unique()[0]
        hashedpronoun=pronoun.text+"#"+Id.split('-')[-1]
        hashedcontext=c[:pronoun.i].text+" "+hashedpronoun+" "+c[pronoun.i+1:].text
        test.append([Id,hashedcontext,hashedpronoun])
    testdf=pd.DataFrame(test,columns=["Id","context","pronoun"])
    test_data = SpanDetectionData(testdf, fast_tokenizer,train=False)
    for param in remodel.base_model.parameters():
        param.requires_grad = False
    re_trainer = Trainer(model=remodel)
    re_predictions=re_trainer.predict(test_data)
    ttruncated_predictions,tpredicted_spans=processPred(re_predictions,test_data,testdf,fast_tokenizer,T=0.9)
    spans=[]
    for i,j in zip(testdf.index, tpredicted_spans):
        spans.append(findspans(testdf.context[i],j))
    testdf['Resolved As']=spans
    return testdf

def preprocess(doc,nlp):
    txt=open(doc,"r").read()
    stext=applynlp(txt,nlp)
    sentences=list(stext.sents)
    pronouns=["I","me","my","mine","myself","you","you","your","yours","yourself","he","him","his","his","himself","she","her","her","hers","herself","it","it","its","itself","we","us","our","ours","ourselves","you","you","your","yours","yourselves","they","them","their","theirs","themselves"]
    li=[]
    i,j=1,1
    ids=[]
    for k in range(0,len(sentences)):
        sent1=sentences[k]
        for pronoun in findPronouns(sent1,pronouns):
            Id=str(i)+"-"+pronoun.text.lower()+"-"+str(j)
            if Id in ids:
                j+=1
                Id=str(i)+"-"+pronoun.text.lower()+"-"+str(j)
            context=[sent1] if k==0 else [sentences[k-1],sent1]
            fullcontext = stext[context[0].start:context[-1].end]
            # contextstr=[sent1.text] if k==0 else [sentences[k-1].text,sent1.text]
            for candidateAntecedent in getNPsFromContext(context,pronoun):
                li.append([Id, context,fullcontext, pronoun, pronoun.i, candidateAntecedent])
                ids.append(Id)
        i+=1
    df=pd.DataFrame(li,columns=["Id","sents","Context","Pronoun","Position","Candidate Antecedent"])
    return df

if __name__ == '__main__':
    nlp = spacy.load("en_core_web_sm")
    sys.exit(main())
